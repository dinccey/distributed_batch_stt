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
from getpass import getpass

# Configuration - CHANGE THESE
SERVER_URL = 'https://your.server.example.com'  # Include protocol, no trailing slash
USERNAME = 'your_username'  # Or input('Username: ')
PASSWORD = 'your_password'  # Or getpass('Password: ')
GET_ENDPOINT = '/task'
POST_ENDPOINT = '/result'

auth = (USERNAME, PASSWORD)
get_url = SERVER_URL + GET_ENDPOINT
post_url = SERVER_URL + POST_ENDPOINT

while True:
    try:
        response = requests.get(get_url, auth=auth, stream=True)
        
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
        
        # Save MP3
        with open('temp.mp3', 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # Convert MP3 to WAV (required for whisper.cpp)
        subprocess.check_call([
            'ffmpeg', '-i', 'temp.mp3', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', 'temp.wav'
        ])
        
        # Run whisper.cpp to generate VTT
        subprocess.check_call([
            './whisper.cpp/build/bin/whisper-cli', '-m', 'models/ggml-medium.bin', '-f', 'temp.wav', '--language', language, '-ovtt'
        ])
        
        # Read generated VTT (whisper.cpp outputs to <input>.vtt, i.e., temp.vtt)
        with open('temp.vtt', 'r') as f:
            vtt_content = f.read()
        
        # Post result
        post_data = {'id': task_id, 'vtt': vtt_content}
        post_response = requests.post(post_url, json=post_data, auth=auth)
        if post_response.status_code != 200:
            print(f"Error posting result: {post_response.status_code}")
        else:
            print("Result posted successfully")
        
        # Cleanup temp files
        for temp_file in ['temp.mp3', 'temp.wav', 'temp.vtt']:
            if os.path.exists(temp_file):
                os.remove(temp_file)
    
    except Exception as e:
        print(f"Exception in loop: {e}")
        time.sleep(10)