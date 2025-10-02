# server.py
# This is the server script using FastAPI. Run with: uvicorn server:app --reload
# Assume you have FastAPI and uvicorn installed: pip install fastapi uvicorn
# Change AUDIO_DIR to your actual directory containing MP3 files (recursive).
# The server does not handle authentication; assume Caddy proxy handles BASIC AUTH.
# The database is a simple text file 'inprogress.txt' listing file paths in progress.
# Lock uses a simple file-based lock with 4-second timeout.
# Logs are in ./logs/YYYY-MM-DD.log

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, Response
import hashlib
import time
import os
import datetime
from pathlib import Path
import json

app = FastAPI()

AUDIO_DIR = '/mnt/data/video'  # CHANGE THIS TO YOUR DIRECTORY
DB_FILE = 'inprogress.txt'
LOCK_FILE = 'lock.file'
LOG_DIR = './logs'

def log_message(msg: str):
    today = datetime.date.today().isoformat()
    Path(LOG_DIR).mkdir(exist_ok=True)
    log_file = Path(LOG_DIR) / f"{today}.log"
    with open(log_file, 'a') as f:
        f.write(f"{datetime.datetime.now().isoformat()} - {msg}\n")

def acquire_lock(lock_file: str, timeout: float = 4.0) -> int:
    start = time.time()
    while time.time() - start < timeout:
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            return fd
        except FileExistsError:
            time.sleep(0.1)
    raise TimeoutError("Failed to acquire lock within timeout")

def release_lock(fd: int, lock_file: str):
    os.close(fd)
    os.remove(lock_file)

def find_file_to_process(root_dir: str) -> Path | None:
    in_progress = set()
    if Path(DB_FILE).exists():
        with open(DB_FILE, 'r') as f:
            in_progress = {line.strip() for line in f}
    for file in sorted(Path(root_dir).rglob('*.mp3')):
        path_str = str(file)
        vtt = file.with_suffix('.vtt')
        if not vtt.exists() and path_str not in in_progress:
            return file
    return None

@app.get("/task")
def get_task(request: Request):
    file = find_file_to_process(AUDIO_DIR)
    if file is None:
        log_message(f"No available file for IP: {request.client.host}")
        return Response(status_code=204)
    
    json_file = file.with_suffix('.json')
    if not json_file.exists():
        log_message(f"Missing JSON for {file} from IP: {request.client.host}")
        raise HTTPException(status_code=500, detail="Missing language JSON")
    
    lang_data = json.loads(json_file.read_text())
    lang = lang_data.get('language')
    if not lang:
        log_message(f"Missing language key in JSON for {file} from IP: {request.client.host}")
        raise HTTPException(status_code=500, detail="Missing language key")
    
    path_str = str(file)
    id_ = hashlib.md5(path_str.encode()).hexdigest()
    
    fd = acquire_lock(LOCK_FILE)
    try:
        with open(DB_FILE, 'a') as f:
            f.write(path_str + '\n')
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
            paths = [line.strip() for line in f]
        
        matching_path = None
        for p in paths:
            if hashlib.md5(p.encode()).hexdigest() == id_:
                matching_path = p
                break
        
        if not matching_path:
            raise HTTPException(status_code=404, detail="ID not found")
        
        # Remove from DB
        paths.remove(matching_path)
        with open(DB_FILE, 'w') as f:
            for p in paths:
                f.write(p + '\n')
    
    finally:
        release_lock(fd, LOCK_FILE)
    
    vtt_path = Path(matching_path).with_suffix('.vtt')
    vtt_path.write_text(vtt_content)
    
    log_message(f"Processed file {matching_path} (ID: {id_}), saved VTT to {vtt_path} from IP: {request.client.host}")
    
    return {"status": "ok"}