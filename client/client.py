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
# For cron scheduling support: pip install croniter

import requests
import subprocess
import time
import os
import csv
from getpass import getpass
import signal
import sys
import argparse
from datetime import datetime
import croniter  # pip install croniter for cron scheduling support

# Configuration - CHANGE THESE
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")  # Include protocol, no trailing slash
AUTH_ENABLED = os.getenv("AUTH_ENABLED", 'false').lower() == 'true'
USERNAME = os.getenv("USERNAME")
PASSWORD = None
CRON_SCHEDULE = os.getenv("CRON")
try:
    PROCESSING_HOURS = float(os.getenv("PROCESSING_HOURS", "1"))
except ValueError:
    PROCESSING_HOURS = 1.0
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

# For robustness, create directories for storing processed files and failed reports
os.makedirs('processed_uploaded', exist_ok=True)
os.makedirs('processed_not_uploaded', exist_ok=True)
os.makedirs('not_processed_failed_report', exist_ok=True)

# Parse command line arguments
parser = argparse.ArgumentParser(description='Client handler for task processing')
parser.add_argument('--retry_failed', action='store_true', help='Run in mode to retry failed uploads and error reports')
args = parser.parse_args()

current_task_id = None
current_language = None
current_audio_minutes = 0.0
current_time_taken = 0.0
current_start_time = None
current_process = None
current_files = []

interrupted = False

def signal_handler(sig, frame):
    global interrupted
    print("Interrupted! The current processing will finish before exiting.")
    interrupted = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

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

def retry_failed():
    """Retry failed VTT uploads and error reports based on folder contents."""
    failed_report_dir = 'not_processed_failed_report'
    not_uploaded_dir = 'processed_not_uploaded'
    uploaded_dir = 'processed_uploaded'

    # First, retry VTT uploads from processed_not_uploaded
    if os.path.exists(not_uploaded_dir):
        for filename in os.listdir(not_uploaded_dir):
            if filename.endswith('.vtt'):
                task_id = filename[:-4]  # remove .vtt
                vtt_path = os.path.join(not_uploaded_dir, filename)
                with open(vtt_path, 'r') as f:
                    vtt_content = f.read()

                posted = False
                for attempt in range(3):
                    try:
                        post_data = {'id': task_id, 'vtt': vtt_content}
                        post_kwargs = {'json': post_data}
                        if auth:
                            post_kwargs['auth'] = auth
                        post_response = requests.post(post_url, **post_kwargs)
                        if post_response.status_code == 200:
                            print(f"Successfully retried upload for {task_id}")
                            posted = True
                            break
                        else:
                            print(f"VTT retry attempt {attempt+1} for {task_id} failed with status {post_response.status_code}")
                    except Exception as pe:
                        print(f"VTT retry attempt {attempt+1} for {task_id} exception: {pe}")
                    time.sleep(5)

                if posted:
                    # Move to uploaded
                    os.rename(vtt_path, os.path.join(uploaded_dir, filename))
                    # Remove corresponding failed report file if exists (since now successful)
                    failed_report_path = os.path.join(failed_report_dir, task_id)
                    if os.path.exists(failed_report_path):
                        os.remove(failed_report_path)
                else:
                    print(f"Failed to retry VTT upload for {task_id} after 3 attempts")
                    # Since VTT post failed again, try to report error
                    error_post_data = {'id': task_id}
                    error_post_kwargs = {'json': error_post_data}
                    if auth:
                        error_post_kwargs['auth'] = auth
                    try:
                        error_response = requests.post(error_url, **error_post_kwargs)
                        if error_response.status_code == 200:
                            print(f"Error reported to server for {task_id} after VTT retry failure")
                        else:
                            print(f"Failed to report error for {task_id}: {error_response.status_code}")
                            # Create empty file if not exists
                            if not os.path.exists(failed_report_path):
                                with open(failed_report_path, 'w') as _:
                                    pass
                    except Exception as ee:
                        print(f"Exception reporting error for {task_id}: {ee}")
                        # Create empty file if not exists
                        if not os.path.exists(failed_report_path):
                            with open(failed_report_path, 'w') as _:
                                pass

    # Then, retry remaining error reports from not_processed_failed_report
    if os.path.exists(failed_report_dir):
        for filename in os.listdir(failed_report_dir):
            task_id = filename  # no extension
            failed_report_path = os.path.join(failed_report_dir, filename)
            error_post_data = {'id': task_id}
            error_post_kwargs = {'json': error_post_data}
            if auth:
                error_post_kwargs['auth'] = auth

            reported = False
            for attempt in range(3):
                try:
                    error_response = requests.post(error_url, **error_post_kwargs)
                    if error_response.status_code == 200:
                        print(f"Successfully retried error report for {task_id}")
                        reported = True
                        break
                    else:
                        print(f"Error retry attempt {attempt+1} for {task_id} failed with status {error_response.status_code}")
                except Exception as ee:
                    print(f"Error retry attempt {attempt+1} for {task_id} exception: {ee}")
                time.sleep(5)

            if reported:
                os.remove(failed_report_path)
            else:
                print(f"Failed to retry error report for {task_id} after 3 attempts")

def process_loop(check_timeout=None):
    global current_task_id, current_language, current_audio_minutes, current_time_taken, current_start_time, current_process, current_files
    while True:
        if check_timeout and check_timeout():
            print(f"Processing window expired after {PROCESSING_HOURS} hours.")
            break

        try:
            kwargs = {'stream': True}
            if auth:
                kwargs['auth'] = auth
            response = requests.get(get_url, **kwargs)
            
            if response.status_code == 204:
                print("No tasks available, sleeping 10s...")
                time.sleep(10)
                if interrupted:
                    break
                continue
            
            if response.status_code != 200:
                print(f"Error getting task: {response.status_code}")
                time.sleep(10)
                if interrupted:
                    break
                continue
            
            task_id = response.headers.get('X-Task-ID')
            language = response.headers.get('X-Language')
            if not task_id or not language:
                print("Missing ID or language in response")
                time.sleep(10)
                if interrupted:
                    break
                continue
            
            print(f"Received task ID: {task_id}, Language: {language}")
            
            mp3_file = f"{task_id}.mp3"
            wav_file = f"{task_id}.wav"
            vtt_file = f"{wav_file}.vtt"
            
            current_files = [mp3_file, wav_file, vtt_file]
            current_task_id = task_id
            current_language = language
            current_audio_minutes = 0.0
            current_time_taken = 0.0
            current_start_time = None
            
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
                current_audio_minutes = audio_seconds / 60
                
                # Convert MP3 to WAV
                ffmpeg_cmd = ['ffmpeg', '-y', '-i', mp3_file, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', wav_file]
                process = subprocess.Popen(ffmpeg_cmd)
                current_process = process
                process.wait()
                current_process = None
                if process.returncode != 0:
                    raise subprocess.CalledProcessError(process.returncode, ffmpeg_cmd)
                
                # Run whisper.cpp
                current_start_time = time.time()
                whisper_cmd = [
                    './main',
                    '-m',
                    './models/ggml-medium.bin',
                    '--language',
                    language,
                    '--vad',
                    '--vad-model',
                    './models/ggml-silero-v5.1.2.bin',
                    '-bs',
                    '5',
                    '--entropy-thold',
                    '2.8',
                    '--max-context',
                    '64',
                    '-f',
                    wav_file,
                    '-ovtt'
                ]
                process = subprocess.Popen(whisper_cmd)
                current_process = process
                process.wait()
                current_process = None
                if process.returncode != 0:
                    raise subprocess.CalledProcessError(process.returncode, whisper_cmd)
                end_time = time.time()
                current_time_taken = end_time - current_start_time
                current_start_time = None
                time_taken = current_time_taken
                
                # Read VTT
                with open(vtt_file, 'r') as f:
                    vtt_content = f.read()
            
            except Exception as e:
                print(f"Processing failed: {e}")
                log_to_csv(task_id, language, current_time_taken, current_audio_minutes, "failure", str(e))
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
                        # If failed to report error, create empty file in not_processed_failed_report
                        with open(os.path.join('not_processed_failed_report', task_id), 'w') as _:
                            pass
                except Exception as ee:
                    print(f"Exception reporting error: {ee}")
                    # If failed to report error, create empty file in not_processed_failed_report
                    with open(os.path.join('not_processed_failed_report', task_id), 'w') as _:
                        pass
                # Cleanup
                cleanup_files(current_files)
                time.sleep(10)
                if interrupted:
                    break
                current_task_id = None
                current_language = None
                current_audio_minutes = 0.0
                current_time_taken = 0.0
                current_start_time = None
                current_files = []
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
                log_to_csv(task_id, language, time_taken, current_audio_minutes, "success", "")
                # For robustness, move the successfully uploaded VTT to processed_uploaded
                if os.path.exists(vtt_file):
                    os.rename(vtt_file, os.path.join('processed_uploaded', f"{task_id}.vtt"))
            else:
                log_to_csv(task_id, language, time_taken, current_audio_minutes, "failure", "Failed to post result after 3 attempts")
                # For robustness, if POST fails, move the VTT to processed_not_uploaded to avoid losing work
                if os.path.exists(vtt_file):
                    os.rename(vtt_file, os.path.join('processed_not_uploaded', f"{task_id}.vtt"))
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
                        # If failed to report error, create empty file in not_processed_failed_report
                        with open(os.path.join('not_processed_failed_report', task_id), 'w') as _:
                            pass
                except Exception as ee:
                    print(f"Exception reporting error: {ee}")
                    # If failed to report error, create empty file in not_processed_failed_report
                    with open(os.path.join('not_processed_failed_report', task_id), 'w') as _:
                        pass
            
            # Cleanup remaining files (MP3 and WAV; VTT is moved)
            cleanup_files([mp3_file, wav_file])
            # If VTT was not moved (unexpected), clean it up
            if os.path.exists(vtt_file):
                os.remove(vtt_file)
            current_task_id = None
            current_language = None
            current_audio_minutes = 0.0
            current_time_taken = 0.0
            current_start_time = None
            current_files = []
            
            if interrupted:
                break
        
        except Exception as e:
            print(f"Exception in loop: {e}")
            time.sleep(10)
            if interrupted:
                break

if args.retry_failed:
    retry_failed()
else:
    if not CRON_SCHEDULE:
        # No cron schedule: run continuously
        process_loop()
    else:
        # Cron schedule provided: use scheduled processing windows
        cron = croniter.croniter(CRON_SCHEDULE, datetime.now())
        while True:
            next_run = cron.get_next(datetime)
            now = datetime.now()
            delta = next_run - now
            if delta.total_seconds() > 0:
                print(f"Next processing at {next_run.strftime('%Y-%m-%d %H:%M:%S')}, sleeping {delta.total_seconds():.0f} seconds")
                time.sleep(delta.total_seconds())
            
            session_start = datetime.now()
            print(f"Starting processing window at {session_start.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Define timeout check
            def check_timeout():
                now = datetime.now()
                elapsed_seconds = (now - session_start).total_seconds()
                return PROCESSING_HOURS > 0 and elapsed_seconds / 3600 > PROCESSING_HOURS
            
            # Now run the processing loop until time's up or interrupted
            process_loop(check_timeout=check_timeout)
            
            if interrupted:
                break