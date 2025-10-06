# client.py
# This is the client handler script. Run with: python client.py
# Assume you have requests installed: pip install requests
# The client loops indefinitely, polling for tasks every 10 seconds if none available.
# It uses BASIC AUTH; change USERNAME and PASSWORD or use input.
# Assume ffmpeg is installed on the system for MP3 to WAV conversion.
# Assume whisper.cpp is compiled and the executable is './main' in the current directory.
# Assume the medium model is in './models/ggml-medium.bin'.
# For GPU support: Compile whisper.cpp with appropriate flags (e.g., -DGGML_CUDA=1 for NVIDIA).
# If GPU not available, it falls back to CPU if built accordingly; for simplicity, assume CPU works.
# Build instructions for whisper.cpp (CPU):
# git clone https://github.com/ggerganov/whisper.cpp
# cd whisper.cpp
# ./models/download-ggml-model.sh medium
# make
# The executable is './main'
# For NVIDIA: make GGML_CUDA=1
# For AMD/Vulkan: make GGML_VULKAN=1 (may require additional setup)

import requests
import subprocess
import time
import os
import csv
from getpass import getpass

# Configuration - CHANGE THESE
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")  # Include protocol, no trailing slash
AUTH_ENABLED = os.getenv("AUTH_ENABLED", 'false').lower() == 'true'
USERNAME = os.getenv("USERNAME")
PASSWORD = None
auth = None

if AUTH_ENABLED:
    if not USERNAME:
        raise ValueError("AUTH_ENABLED is true but USERNAME environment variable is not set.")
    PASSWORD = os.getenv("PASSWORD") or getpass("Password: ")
    auth = (USERNAME, PASSWORD)

GET_ENDPOINT = '/task'
POST_ENDPOINT = '/result'
ERROR_ENDPOINT = '/error'

get_url = SERVER_URL + GET_ENDPOINT
post_url = SERVER_URL + POST_ENDPOINT
error_url = SERVER_URL + ERROR_ENDPOINT

def log_to_csv(file_id, language, time_taken, audio_minutes, status, reason):
    fieldnames = ['file_id', 'language', 'time_taken', 'audio_minutes', 'status', 'reason']
    file_exists = os.path.exists('processed.csv')
    with open('processed.csv', 'a', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            'file_id': file_id,
            'language': language,
            'time_taken': time_taken,
            'audio_minutes': audio_minutes,
            'status': status,
            'reason': reason
        })

def cleanup_files(files):
    for f in files:
        if os.path.exists(f):
            os.remove(f)

while True:
    try:
        kwargs = {'stream': True}
        if auth:
            kwargs['auth'] = auth
        response = requests.get(get_url, **kwargs)
        
        if response.status_code == 204:
            print("No tasks available, sleeping 10s...")
            time.sleep(10)
            continue
        
        if response.status_code != 200:
            print(f"Error getting task: {response.status_code}")
            time.sleep(10)
            continue
        
        task_id = response.headers.get('X-Task-ID')
        language = response.headers.get('X-Language')
        if not task_id or not language:
            print("Missing ID or language in response")
            time.sleep(10)
            continue
        
        print(f"Received task ID: {task_id}, Language: {language}")
        
        mp3_file = f"{task_id}.mp3"
        wav_file = f"{task_id}.wav"
        vtt_file = f"{wav_file}.vtt"
        
        audio_minutes = 0.0
        time_taken = 0.0
        
        try:
            # Save MP3
            with open(mp3_file, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            # Get audio duration using ffprobe
            duration_output = subprocess.check_output([
                'ffprobe', '-i', mp3_file, '-show_entries', 'format=duration', '-v', 'quiet', '-of', 'csv=p=0'
            ])
            audio_seconds = float(duration_output.strip())
            audio_minutes = audio_seconds / 60
            
            # Convert MP3 to WAV
            subprocess.check_call([
                'ffmpeg', '-y', '-i', mp3_file, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', wav_file
            ])
            
            # Run whisper.cpp
            start_time = time.time()
            subprocess.check_call([
                './whisper/whisper.cpp/build/bin/whisper-cli', '-m', './whisper/whisper.cpp/models/ggml-medium.bin', '-f', wav_file, '--language', language, '-ovtt'
            ])
            end_time = time.time()
            time_taken = end_time - start_time
            
            # Read VTT
            with open(vtt_file, 'r') as f:
                vtt_content = f.read()
        
        except Exception as e:
            print(f"Processing failed: {e}")
            log_to_csv(task_id, language, time_taken, audio_minutes, "failure", str(e))
            # Send to /error
            error_post_data = {'id': task_id}
            error_post_kwargs = {'json': error_post_data}
            if auth:
                error_post_kwargs['auth'] = auth
            try:
                error_response = requests.post(error_url, **error_post_kwargs)
                if error_response.status_code == 200:
                    print("Error reported to server")
                else:
                    print(f"Failed to report error: {error_response.status_code}")
            except Exception as ee:
                print(f"Exception reporting error: {ee}")
            # Cleanup
            cleanup_files([mp3_file, wav_file, vtt_file])
            time.sleep(10)
            continue
        
        # Post result with retries
        posted = False
        for attempt in range(3):
            try:
                post_data = {'id': task_id, 'vtt': vtt_content}
                post_kwargs = {'json': post_data}
                if auth:
                    post_kwargs['auth'] = auth
                post_response = requests.post(post_url, **post_kwargs)
                if post_response.status_code == 200:
                    print("Result posted successfully")
                    posted = True
                    break
                else:
                    print(f"Post attempt {attempt+1} failed with status {post_response.status_code}")
            except Exception as pe:
                print(f"Post attempt {attempt+1} exception: {pe}")
            time.sleep(5)
        
        if posted:
            log_to_csv(task_id, language, time_taken, audio_minutes, "success", "")
        else:
            log_to_csv(task_id, language, time_taken, audio_minutes, "failure", "Failed to post result after 3 attempts")
            # Save VTT locally
            os.makedirs('failed_vtt', exist_ok=True)
            if os.path.exists(vtt_file):
                os.rename(vtt_file, f"failed_vtt/{task_id}.vtt")
            # Send to /error
            error_post_data = {'id': task_id}
            error_post_kwargs = {'json': error_post_data}
            if auth:
                error_post_kwargs['auth'] = auth
            try:
                error_response = requests.post(error_url, **error_post_kwargs)
                if error_response.status_code == 200:
                    print("Error reported to server due to post failure")
                else:
                    print(f"Failed to report error: {error_response.status_code}")
            except Exception as ee:
                print(f"Exception reporting error: {ee}")
        
        # Cleanup remaining files
        cleanup_files([mp3_file, wav_file, vtt_file])
    
    except Exception as e:
        print(f"Exception in loop: {e}")
        time.sleep(10)