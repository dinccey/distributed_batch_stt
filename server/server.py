# server.py
# This is the server script using FastAPI. Run with: uvicorn server:app --reload
# Assume you have FastAPI and uvicorn installed: pip install fastapi uvicorn
# Change AUDIO_DIR to your actual directory containing MP3 files (recursive).
# The server does not handle authentication; assume Caddy proxy handles BASIC AUTH.
# The database is now SQLite 'tasks.db' for managing task states.
# Logs are in ./logs/YYYY-MM-DD.log
# No more file-based lock; rely on SQLite for concurrency.

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, Response
import hashlib
import time
import os
import datetime
from pathlib import Path
import json
import csv
import sqlite3
import threading

app = FastAPI()

AUDIO_DIR = os.getenv("AUDIO_DIR", '/mnt/data/video')  # CHANGE THIS TO YOUR DIRECTORY
DB_FILE = os.getenv("DB_FILE", 'tasks.db')
LOG_DIR = os.getenv("LOG_DIR", './logs')
CSV_FILE = 'processed.csv'

TASK_TIMEOUT = 360000  # 100 hour for task expiration; adjust as needed
SYNC_INTERVAL = 300  # 5 minutes for directory sync

def log_message(msg: str):
    today = datetime.date.today().isoformat()
    Path(LOG_DIR).mkdir(exist_ok=True)
    log_file = Path(LOG_DIR) / f"{today}.log"
    with open(log_file, 'a') as f:
        f.write(f"{datetime.datetime.now().isoformat()} - {msg}\n")

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=10.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            path TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            assigned_at REAL,
            assigned_ip TEXT,
            task_id TEXT
        )
    ''')
    conn.commit()
    conn.close()

def clean_expired():
    conn = get_db_connection()
    cur = conn.cursor()
    now = time.time()
    cur.execute("""
        UPDATE tasks
        SET status = 'failed', assigned_at = NULL, assigned_ip = NULL, task_id = NULL
        WHERE status = 'in_progress' AND ? - assigned_at > ?
    """, (now, TASK_TIMEOUT))
    if cur.rowcount > 0:
        log_message(f"Expired {cur.rowcount} stale tasks")
    conn.commit()
    conn.close()

def set_task_failed(path: str, error: str = 'Unknown error'):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE tasks
        SET status = 'failed', assigned_at = NULL, assigned_ip = NULL, task_id = NULL
        WHERE path = ?
    """, (path,))
    conn.commit()
    conn.close()

def sync_dir_with_db():
    log_message("Starting directory sync")
    conn = get_db_connection()
    cur = conn.cursor()
    added = 0
    reset = 0
    for dirpath, dirnames, filenames in os.walk(AUDIO_DIR):
        for filename in filenames:
            if filename.lower().endswith('.mp3'):
                path_str = os.path.join(dirpath, filename)
                vtt_path = os.path.splitext(path_str)[0] + '.vtt'
                if not os.path.exists(vtt_path):
                    cur.execute("SELECT status, assigned_at FROM tasks WHERE path = ?", (path_str,))
                    row = cur.fetchone()
                    do_reset = False
                    if row is None:
                        cur.execute("INSERT INTO tasks (path, status) VALUES (?, 'pending')", (path_str,))
                        added += 1
                    else:
                        current_status = row[0]
                        if current_status == 'pending':
                            continue
                        if current_status == 'in_progress':
                            if time.time() - row[1] > TASK_TIMEOUT:
                                do_reset = True
                                log_message(f"Expiring in_progress task during sync: {path_str}")
                            else:
                                continue
                        else:  # failed or completed, but vtt missing, reset
                            do_reset = True
                        if do_reset:
                            cur.execute("""
                                UPDATE tasks
                                SET status = 'pending', assigned_at = NULL, assigned_ip = NULL, task_id = NULL
                                WHERE path = ?
                            """, (path_str,))
                            reset += 1
                            log_message(f"Reset task to pending: {path_str}")
    conn.commit()
    conn.close()
    log_message(f"Directory sync completed: {added} added, {reset} reset")

def periodic_sync():
    while True:
        sync_dir_with_db()
        time.sleep(SYNC_INTERVAL)

def find_file_to_process(ip: str) -> Path | None:
    clean_expired()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT path FROM tasks WHERE status = 'pending' LIMIT 1")
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    path_str = row[0]
    id_ = hashlib.md5(path_str.encode()).hexdigest()
    cur.execute("""
        UPDATE tasks
        SET status = 'in_progress', assigned_at = ?, assigned_ip = ?, task_id = ?
        WHERE path = ? AND status = 'pending'
    """, (time.time(), ip, id_, path_str))
    conn.commit()
    conn.close()
    return Path(path_str)

def log_to_csv(filepath: str, fileid: str, ip: str, error: str):
    now = datetime.datetime.now().isoformat()
    with open(CSV_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
            writer.writerow(['filepath', 'fileid', 'ip', 'datetime', 'error'])
        writer.writerow([filepath, fileid, ip, now, error])

@app.on_event("startup")
def startup_event():
    init_db()
    # Initial sync (blocking, but ok for startup)
    sync_dir_with_db()
    # Start periodic sync in background
    thread = threading.Thread(target=periodic_sync, daemon=True)
    thread.start()

@app.get("/task")
def get_task(request: Request):
    ip = request.client.host
    attempts = 0
    max_attempts = 10  # Increased slightly for safety
    while attempts < max_attempts:
        file = find_file_to_process(ip)
        if file is None:
            log_message(f"No available file for IP: {ip}")
            return Response(status_code=204)
        
        path_str = str(file)
        id_ = hashlib.md5(path_str.encode()).hexdigest()
        json_file = file.with_suffix('.json')
        
        try:
            if not json_file.exists():
                raise ValueError("Missing JSON")
            
            lang_data = json.loads(json_file.read_text())
            lang = lang_data.get('sql_params').get('language')
            if not lang:
                raise ValueError("Missing language key")
        
        except Exception as e:
            error_str = str(e)
            log_message(f"Error with JSON for {file}: {error_str} from IP: {ip}")
            log_to_csv(path_str, id_, ip, error_str)
            set_task_failed(path_str, error_str)
            attempts += 1
            continue
        
        # If we reach here, the file is good
        log_message(f"Assigned file {path_str} (ID: {id_}, Lang: {lang}) to IP: {ip}")
        
        headers = {
            'X-Task-ID': id_,
            'X-Language': lang
        }
        
        def iterfile():
            with open(file, "rb") as f:
                while chunk := f.read(8192):
                    yield chunk
        
        return StreamingResponse(iterfile(), media_type="audio/mpeg", headers=headers)
    
    # If max attempts reached, return no content
    log_message(f"Max attempts reached for IP: {ip}")
    return Response(status_code=204)

@app.post("/result")
async def post_result(request: Request):
    data = await request.json()
    id_ = data.get('id')
    vtt_content = data.get('vtt')
    if not id_ or not vtt_content:
        log_message(f"Invalid POST data from IP: {request.client.host}")
        raise HTTPException(status_code=400, detail="Missing id or vtt")
    
    ip = request.client.host
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT path FROM tasks WHERE task_id = ? AND status = 'in_progress'", (id_,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="ID not found")
    
    matching_path = row[0]
    cur.execute("""
        UPDATE tasks
        SET status = 'completed', assigned_at = NULL, assigned_ip = NULL, task_id = NULL
        WHERE path = ?
    """, (matching_path,))
    conn.commit()
    conn.close()
    
    vtt_path = Path(matching_path).with_suffix('.vtt')
    vtt_path.write_text(vtt_content)
    
    log_to_csv(matching_path, id_, ip, "")
    
    log_message(f"Processed file {matching_path} (ID: {id_}), saved VTT to {vtt_path} from IP: {ip}")
    
    return {"status": "ok"}

@app.post("/error")
async def post_error(request: Request):
    data = await request.json()
    id_ = data.get('id')
    error_msg = data.get('error', 'Unknown error')
    if not id_:
        log_message(f"Invalid POST data for /error from IP: {request.client.host}")
        raise HTTPException(status_code=400, detail="Missing id")
    
    ip = request.client.host
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT path FROM tasks WHERE task_id = ? AND status = 'in_progress'", (id_,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="ID not found")
    
    matching_path = row[0]
    cur.execute("""
        UPDATE tasks
        SET status = 'failed', assigned_at = NULL, assigned_ip = NULL, task_id = NULL
        WHERE path = ?
    """, (matching_path,))
    conn.commit()
    conn.close()
    
    log_to_csv(matching_path, id_, ip, error_msg)
    
    log_message(f"Error reported for {matching_path} (ID: {id_}): {error_msg} from IP: {ip}, set to failed.")
    
    return {"status": "ok"}