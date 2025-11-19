# server.py
# This is the server script using FastAPI. Run with: uvicorn server:app --reload
# Assume you have FastAPI and uvicorn installed: pip install fastapi uvicorn
# Change AUDIO_DIR to your actual directory containing MP3 files (recursive).
# The server does not handle authentication; assume Caddy proxy handles BASIC AUTH.
# The database is a simple text file 'DB.txt' listing id:path:timestamp pairs for tasks in progress.
# Lock uses a simple file-based lock with 4-second timeout and stale lock detection.
# Logs are in ./logs/YYYY-MM-DD.log

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, Response
import hashlib
import time
import os
import datetime
from pathlib import Path
import json
import csv
import stat  # For st_mtime

app = FastAPI()

AUDIO_DIR = os.getenv("AUDIO_DIR", '/mnt/data/video')  # CHANGE THIS TO YOUR DIRECTORY
DB_FILE = os.getenv("DB_FILE", 'inprogress.txt')
LOCK_FILE = os.getenv("LOCK_FILE", 'lock.file')
LOG_DIR = os.getenv("LOG_DIR", './logs')
CSV_FILE = 'processed.csv'
FAILED_FILE = 'failed.txt'

TASK_TIMEOUT = 360000  # 100 hour for task expiration
STALE_LOCK_TIMEOUT = 10  # Seconds to consider a lock stale

def log_message(msg: str):
    today = datetime.date.today().isoformat()
    Path(LOG_DIR).mkdir(exist_ok=True)
    log_file = Path(LOG_DIR) / f"{today}.log"
    with open(log_file, 'a') as f:
        f.write(f"{datetime.datetime.now().isoformat()} - {msg}\n")

def acquire_lock(lock_file: str, timeout: float = 4.0) -> int:
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(lock_file):
            try:
                stat_info = os.stat(lock_file)
                if time.time() - stat_info.st_mtime > STALE_LOCK_TIMEOUT:
                    os.remove(lock_file)
                    log_message(f"Removed stale lock file: {lock_file}")
            except Exception as e:
                log_message(f"Error checking/removing stale lock: {str(e)}")
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            return fd
        except FileExistsError:
            time.sleep(0.1)
    raise TimeoutError("Failed to acquire lock within timeout")

def release_lock(fd: int, lock_file: str):
    os.close(fd)
    os.remove(lock_file)

def log_to_csv(filepath: str, fileid: str, ip: str, error: str):
    now = datetime.datetime.now().isoformat()
    with open(CSV_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
            writer.writerow(['filepath', 'fileid', 'ip', 'datetime', 'error'])
        writer.writerow([filepath, fileid, ip, now, error])

def add_to_failed(path: str):
    fd = acquire_lock(LOCK_FILE)
    try:
        with open(FAILED_FILE, 'a') as f:
            f.write(f"{path}\n")
    finally:
        release_lock(fd, LOCK_FILE)

def find_file_to_process(root_dir: str) -> Path | None:
    in_progress = {}
    if Path(DB_FILE).exists():
        with open(DB_FILE, 'r') as f:
            for line in f:
                line_strip = line.strip()
                if not line_strip:
                    continue
                parts = line_strip.split(':')
                if len(parts) >= 2:
                    id_ = parts[0]
                    if len(parts) >= 3:
                        ts_str = parts[-1]
                        path = ':'.join(parts[1:-1])
                    else:
                        # Backward compat for old entries without ts (treat as expired later)
                        ts_str = "0"
                        path = ':'.join(parts[1:])
                    try:
                        ts = float(ts_str)
                        in_progress[path] = ts
                    except ValueError:
                        log_message(f"Invalid timestamp in DB line: {line_strip}")
    failed = set()
    if Path(FAILED_FILE).exists():
        with open(FAILED_FILE, 'r') as f:
            for line in f:
                failed.add(line.strip())
    # Clean expired tasks
    now = time.time()
    expired_paths = [path for path, ts in in_progress.items() if now - ts > TASK_TIMEOUT]
    if expired_paths:
        fd = acquire_lock(LOCK_FILE)
        try:
            with open(DB_FILE, 'r') as f:
                lines = f.readlines()
            new_lines = []
            for l in lines:
                l_strip = l.strip()
                if not l_strip:
                    new_lines.append(l)
                    continue
                parts = l_strip.split(':')
                if len(parts) >= 2:
                    if len(parts) >= 3:
                        path = ':'.join(parts[1:-1])
                    else:
                        path = ':'.join(parts[1:])
                    if path not in expired_paths:
                        new_lines.append(l)
            with open(DB_FILE, 'w') as f:
                f.writelines(new_lines)
            for path in expired_paths:
                add_to_failed(path)
                log_message(f"Expired stale task and added to failed: {path}")
        finally:
            release_lock(fd, LOCK_FILE)
    # Update in_progress after cleanup
    in_progress = {p: t for p, t in in_progress.items() if p not in expired_paths}
    # Now scan for available file (generator, no sorted for efficiency)
    for file in Path(root_dir).rglob('*.mp3'):
        path_str = str(file)
        vtt = file.with_suffix('.vtt')
        if not vtt.exists() and path_str not in in_progress and path_str not in failed:
            return file
    return None

@app.get("/task")
def get_task(request: Request):
    attempts = 0
    max_attempts = 3  # Safety limit to prevent infinite loop
    while attempts < max_attempts:
        file = find_file_to_process(AUDIO_DIR)
        if file is None:
            log_message(f"No available file for IP: {request.client.host}")
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
            log_message(f"Error with JSON for {file}: {error_str} from IP: {request.client.host}")
            log_to_csv(path_str, id_, request.client.host, error_str)
            add_to_failed(path_str)
            attempts += 1
            continue
        
        # If we reach here, the file is good
        fd = acquire_lock(LOCK_FILE)
        try:
            with open(DB_FILE, 'a') as f:
                f.write(f"{id_}:{path_str}:{time.time()}\n")
        finally:
            release_lock(fd, LOCK_FILE)
        
        log_message(f"Assigned file {path_str} (ID: {id_}, Lang: {lang}) to IP: {request.client.host}")
        
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
    log_message(f"Max attempts reached for IP: {request.client.host}")
    return Response(status_code=204)

@app.post("/result")
async def post_result(request: Request):
    data = await request.json()
    id_ = data.get('id')
    vtt_content = data.get('vtt')
    if not id_ or not vtt_content:
        log_message(f"Invalid POST data from IP: {request.client.host}")
        raise HTTPException(status_code=400, detail="Missing id or vtt")
    
    fd = acquire_lock(LOCK_FILE)
    try:
        if not Path(DB_FILE).exists():
            raise HTTPException(status_code=404, detail="ID not found")
        
        with open(DB_FILE, 'r') as f:
            lines = f.readlines()
        
        matching_path = None
        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                continue
            parts = line_strip.split(':')
            if len(parts) >= 2 and parts[0] == id_:
                if len(parts) >= 3:
                    matching_path = ':'.join(parts[1:-1])
                else:
                    matching_path = ':'.join(parts[1:])
                break
        
        if not matching_path:
            raise HTTPException(status_code=404, detail="ID not found")
        
        # Remove the line
        new_lines = [l for l in lines if not (l.strip() and l.strip().split(':')[0] == id_)]
        with open(DB_FILE, 'w') as f:
            f.writelines(new_lines)
    
    finally:
        release_lock(fd, LOCK_FILE)
    
    vtt_path = Path(matching_path).with_suffix('.vtt')
    vtt_path.write_text(vtt_content)
    
    log_to_csv(matching_path, id_, request.client.host, "")
    
    log_message(f"Processed file {matching_path} (ID: {id_}), saved VTT to {vtt_path} from IP: {request.client.host}")
    
    return {"status": "ok"}

@app.post("/error")
async def post_error(request: Request):
    data = await request.json()
    id_ = data.get('id')
    error_msg = data.get('error', 'Unknown error')
    if not id_:
        log_message(f"Invalid POST data for /error from IP: {request.client.host}")
        raise HTTPException(status_code=400, detail="Missing id")
    
    fd = acquire_lock(LOCK_FILE)
    try:
        if not Path(DB_FILE).exists():
            raise HTTPException(status_code=404, detail="ID not found")
        
        with open(DB_FILE, 'r') as f:
            lines = f.readlines()
        
        matching_path = None
        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                continue
            parts = line_strip.split(':')
            if len(parts) >= 2 and parts[0] == id_:
                if len(parts) >= 3:
                    matching_path = ':'.join(parts[1:-1])
                else:
                    matching_path = ':'.join(parts[1:])
                break
        
        if not matching_path:
            raise HTTPException(status_code=404, detail="ID not found")
        
        # Remove the line
        new_lines = [l for l in lines if not (l.strip() and l.strip().split(':')[0] == id_)]
        with open(DB_FILE, 'w') as f:
            f.writelines(new_lines)
    
    finally:
        release_lock(fd, LOCK_FILE)
    
    log_to_csv(matching_path, id_, request.client.host, error_msg)
    
    log_message(f"Error reported for {matching_path} (ID: {id_}): {error_msg} from IP: {request.client.host}, removed from DB.")
    
    return {"status": "ok"}