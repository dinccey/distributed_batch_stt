# client.py
# Edited: stream whisper stdout live to CLI, still capture stderr/stdout tails for notifications.

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
import threading
import croniter
from collections import deque

# -----------------------------
# Configuration (from env vars)
# -----------------------------
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")  # Include protocol, no trailing slash
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
USERNAME = os.getenv("USERNAME")
PASSWORD = None
CRON_SCHEDULE = os.getenv("CRON")
try:
    PROCESSING_HOURS = float(os.getenv("PROCESSING_HOURS", "1"))
except ValueError:
    PROCESSING_HOURS = 1.0

# VAD configuration
VAD_ENABLED = os.getenv("VAD_ENABLED", "true").lower() == "true"
VAD_MODEL = os.getenv("VAD_MODEL", "./whisper/whisper.cpp/models/ggml-silero-v5.1.2.bin")

# Gotify configuration
NODE_NAME = os.getenv("NODE_NAME", "UnknownNode")
GOTIFY_URL = os.getenv("GOTIFY_URL")
GOTIFY_KEY = os.getenv("GOTIFY_KEY")

# Endpoints
GET_ENDPOINT = "/task"
POST_ENDPOINT = "/result"
ERROR_ENDPOINT = "/error"

get_url = SERVER_URL + GET_ENDPOINT
post_url = SERVER_URL + POST_ENDPOINT
error_url = SERVER_URL + ERROR_ENDPOINT

# -----------------------------
# Auth setup
# -----------------------------
auth = None
if AUTH_ENABLED:
    if not USERNAME:
        raise ValueError("AUTH_ENABLED is true but USERNAME environment variable is not set.")
    PASSWORD = os.getenv("PASSWORD") or getpass("Password: ")
    auth = (USERNAME, PASSWORD)

# -----------------------------
# Helpers and state
# -----------------------------
def send_gotify_error(title: str, message: str) -> None:
    """Send error notification to Gotify if configured."""
    if GOTIFY_URL and GOTIFY_KEY:
        try:
            resp = requests.post(
                f"{GOTIFY_URL}/message",
                json={"title": title, "message": message, "priority": 7},
                headers={"X-Gotify-Key": GOTIFY_KEY},
                timeout=5,
            )
            if resp.status_code != 200:
                print(f"Gotify error post failed: {resp.status_code}")
        except Exception as e:
            print(f"Exception sending Gotify error: {e}")

def tail_text_from_deque(dq: deque, n: int = 20) -> str:
    """Return last n lines from a deque as a single string."""
    if not dq:
        return ""
    return "\n".join(list(dq)[-n:])

def compose_error_message(console_msg: str, stdout_deque: deque, stderr_deque: deque) -> str:
    """
    Prefer the exact console error message; append tails of stdout/stderr if available.
    """
    out_tail = tail_text_from_deque(stdout_deque, 20)
    err_tail = tail_text_from_deque(stderr_deque, 20)
    parts = [console_msg]
    if out_tail:
        parts.append("--- stdout tail ---\n" + out_tail)
    if err_tail:
        parts.append("--- stderr tail ---\n" + err_tail)
    return "\n".join(parts)

def print_subprocess_output(label: str, stdout_deque: deque, stderr_deque: deque) -> None:
    """Print last 20 lines of captured subprocess output to the CLI."""
    out_tail = tail_text_from_deque(stdout_deque, 20)
    err_tail = tail_text_from_deque(stderr_deque, 20)
    print(f"[{label}] Output tails:")
    if out_tail:
        print("  stdout:")
        for line in out_tail.splitlines():
            print("    " + line)
    else:
        print("  stdout: <empty>")
    if err_tail:
        print("  stderr:")
        for line in err_tail.splitlines():
            print("    " + line)
    else:
        print("  stderr: <empty>")

def stream_subprocess(proc, stdout_deque: deque, stderr_deque: deque, print_stdout_live: bool = False):
    """
    Read stdout and stderr from proc line-by-line in threads.
    If print_stdout_live is True, stdout lines are printed to console as received.
    Both stdout and stderr lines appended to respective deques (bounded).
    """
    def _reader(pipe, dq, print_live):
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                clean = line.rstrip("\n")
                dq.append(clean)
                if print_live:
                    print(clean)
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    threads = []
    if proc.stdout:
        t_out = threading.Thread(target=_reader, args=(proc.stdout, stdout_deque, print_stdout_live), daemon=True)
        t_out.start()
        threads.append(t_out)
    if proc.stderr:
        t_err = threading.Thread(target=_reader, args=(proc.stderr, stderr_deque, False), daemon=True)
        t_err.start()
        threads.append(t_err)

    # Wait for process to finish and for threads to consume pipes
    rc = proc.wait()
    for t in threads:
        t.join(timeout=1)
    return rc

# Ensure directories exist
os.makedirs("processed_uploaded", exist_ok=True)
os.makedirs("processed_not_uploaded", exist_ok=True)
os.makedirs("not_processed_failed_report", exist_ok=True)

# Global runtime state
current_task_id = None
current_language = None
current_audio_minutes = 0.0
current_time_taken = 0.0
current_start_time = None
current_process = None
current_files = []

interrupted = False

# -----------------------------
# Signal handling
# -----------------------------
def signal_handler(sig, frame):
    global interrupted
    print("Interrupted! The current processing will finish before exiting.")
    interrupted = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# -----------------------------
# CSV logging
# -----------------------------
def log_to_csv(file_id, language, time_taken, audio_minutes, status, reason):
    fieldnames = ["file_id", "language", "time_taken", "audio_minutes", "status", "reason"]
    file_exists = os.path.exists("processed.csv")
    with open("processed.csv", "a", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(
            {
                "file_id": file_id,
                "language": language,
                "time_taken": time_taken,
                "audio_minutes": audio_minutes,
                "status": status,
                "reason": reason,
            }
        )

# -----------------------------
# Files cleanup
# -----------------------------
def cleanup_files(files):
    for f in files:
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception as e:
            print(f"Failed to remove {f}: {e}")

# -----------------------------
# Retry logic for failed uploads
# -----------------------------
def retry_failed():
    """Retry failed VTT uploads and error reports based on folder contents."""
    failed_report_dir = "not_processed_failed_report"
    not_uploaded_dir = "processed_not_uploaded"
    uploaded_dir = "processed_uploaded"

    # First, retry VTT uploads from processed_not_uploaded
    if os.path.exists(not_uploaded_dir):
        for filename in os.listdir(not_uploaded_dir):
            if not filename.endswith(".vtt"):
                continue

            task_id = filename[:-4]
            vtt_path = os.path.join(not_uploaded_dir, filename)

            try:
                with open(vtt_path, "r") as f:
                    vtt_content = f.read()
            except Exception as e:
                msg = f"Failed to read VTT for {task_id}: {e}"
                print(msg)
                send_gotify_error(NODE_NAME, msg)
                continue

            posted = False
            for attempt in range(3):
                try:
                    post_data = {"id": task_id, "vtt": vtt_content}
                    post_kwargs = {"json": post_data, "timeout": 10}
                    if auth:
                        post_kwargs["auth"] = auth
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
                try:
                    os.rename(vtt_path, os.path.join(uploaded_dir, filename))
                except Exception as e:
                    print(f"Failed to move VTT to uploaded for {task_id}: {e}")
                failed_report_path = os.path.join(failed_report_dir, task_id)
                if os.path.exists(failed_report_path):
                    try:
                        os.remove(failed_report_path)
                    except Exception as e:
                        print(f"Failed to remove failed report for {task_id}: {e}")
            else:
                msg = f"Failed to retry VTT upload for {task_id} after 3 attempts"
                print(msg)
                send_gotify_error(NODE_NAME, msg)
                error_post_data = {"id": task_id}
                error_post_kwargs = {"json": error_post_data, "timeout": 10}
                if auth:
                    error_post_kwargs["auth"] = auth
                try:
                    error_response = requests.post(error_url, **error_post_kwargs)
                    if error_response.status_code == 200:
                        print(f"Error reported to server for {task_id} after VTT retry failure")
                    else:
                        print(f"Failed to report error for {task_id}: {error_response.status_code}")
                        failed_report_path = os.path.join(failed_report_dir, task_id)
                        if not os.path.exists(failed_report_path):
                            with open(failed_report_path, "w") as _:
                                pass
                except Exception as ee:
                    print(f"Exception reporting error for {task_id}: {ee}")
                    failed_report_path = os.path.join(failed_report_dir, task_id)
                    if not os.path.exists(failed_report_path):
                        with open(failed_report_path, "w") as _:
                            pass

    # Then, retry remaining error reports from not_processed_failed_report
    if os.path.exists(failed_report_dir):
        for filename in os.listdir(failed_report_dir):
            task_id = filename
            failed_report_path = os.path.join(failed_report_dir, filename)
            error_post_data = {"id": task_id}
            error_post_kwargs = {"json": error_post_data, "timeout": 10}
            if auth:
                error_post_kwargs["auth"] = auth

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
                try:
                    os.remove(failed_report_path)
                except Exception as e:
                    print(f"Failed to remove failed report file for {task_id}: {e}")
            else:
                msg = f"Failed to retry error report for {task_id} after 3 attempts"
                print(msg)
                send_gotify_error(NODE_NAME, msg)

# -----------------------------
# Main processing loop
# -----------------------------
def process_loop(check_timeout=None):
    global current_task_id, current_language, current_audio_minutes, current_time_taken, current_start_time, current_process, current_files

    while True:
        if check_timeout and check_timeout():
            print(f"Processing window expired after {PROCESSING_HOURS} hours.")
            break

        try:
            kwargs = {"stream": True, "timeout": 30}
            if auth:
                kwargs["auth"] = auth
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

            task_id = response.headers.get("X-Task-ID")
            language = response.headers.get("X-Language")
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

            # Buffers for subprocess outputs: bounded deques to hold latest lines
            ffmpeg_stdout_deque = deque(maxlen=200)
            ffmpeg_stderr_deque = deque(maxlen=200)
            whisper_stdout_deque = deque(maxlen=1000)
            whisper_stderr_deque = deque(maxlen=1000)

            try:
                # Save MP3
                with open(mp3_file, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

                # Get audio duration using ffprobe
                duration_output = subprocess.check_output(
                    [
                        "ffprobe",
                        "-i",
                        mp3_file,
                        "-show_entries",
                        "format=duration",
                        "-v",
                        "quiet",
                        "-of",
                        "csv=p=0",
                    ],
                    timeout=30,
                )
                audio_seconds = float(duration_output.strip())
                current_audio_minutes = audio_seconds / 60.0

                # Convert MP3 to WAV (capture output)
                ffmpeg_cmd = ["ffmpeg", "-y", "-i", mp3_file, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_file]
                proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                # read ffmpeg output fully (no live streaming) but keep tails
                def _read_all(pipe, dq):
                    try:
                        for line in iter(pipe.readline, ""):
                            if not line:
                                break
                            dq.append(line.rstrip("\n"))
                    except Exception:
                        pass
                    finally:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                t1 = threading.Thread(target=_read_all, args=(proc.stdout, ffmpeg_stdout_deque), daemon=True)
                t2 = threading.Thread(target=_read_all, args=(proc.stderr, ffmpeg_stderr_deque), daemon=True)
                t1.start(); t2.start()
                rc = proc.wait()
                t1.join(timeout=1); t2.join(timeout=1)
                print_subprocess_output("ffmpeg", ffmpeg_stdout_deque, ffmpeg_stderr_deque)
                if rc != 0:
                    raise subprocess.CalledProcessError(rc, ffmpeg_cmd)

                # Build whisper command
                whisper_cmd = [
                    "./whisper/whisper.cpp/build/bin/whisper-cli",
                    "-m",
                    "./whisper/whisper.cpp/models/ggml-medium.bin",
                    "--language",
                    language,
                    "-bs",
                    "5",
                    "--entropy-thold",
                    "2.8",
                    "--max-context",
                    "64",
                    "-f",
                    wav_file,
                    "-ovtt",
                ]
                if VAD_ENABLED:
                    if not os.path.exists(VAD_MODEL):
                        raise FileNotFoundError(f"VAD model not found at {VAD_MODEL}")
                    whisper_cmd.extend(["--vad", "--vad-model", VAD_MODEL])

                # Run whisper and stream stdout live to console while capturing tails
                current_start_time = time.time()
                proc = subprocess.Popen(whisper_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                current_process = proc
                # stream_subprocess reads stdout and stderr concurrently, printing stdout live
                rc = stream_subprocess(proc, whisper_stdout_deque, whisper_stderr_deque, print_stdout_live=True)
                current_process = None
                if rc != 0:
                    # print captured tails for debugging
                    print_subprocess_output("whisper (on failure)", whisper_stdout_deque, whisper_stderr_deque)
                    raise subprocess.CalledProcessError(rc, whisper_cmd)

                end_time = time.time()
                current_time_taken = end_time - current_start_time
                current_start_time = None
                time_taken = current_time_taken

                # Read VTT
                with open(vtt_file, "r") as f:
                    vtt_content = f.read()

            except Exception as e:
                console_msg = f"Processing failed: {e}"
                print(console_msg)
                # Compose notification with captured tails (whisper preferred)
                notif_msg = compose_error_message(console_msg, whisper_stdout_deque or ffmpeg_stdout_deque, whisper_stderr_deque or ffmpeg_stderr_deque)
                log_to_csv(task_id, language, current_time_taken, current_audio_minutes, "failure", str(e))
                send_gotify_error(NODE_NAME, notif_msg)

                # Report error to server
                error_post_data = {"id": task_id}
                error_post_kwargs = {"json": error_post_data, "timeout": 10}
                if auth:
                    error_post_kwargs["auth"] = auth
                try:
                    error_response = requests.post(error_url, **error_post_kwargs)
                    if error_response.status_code == 200:
                        print("Error reported to server")
                    else:
                        print(f"Failed to report error: {error_response.status_code}")
                        failed_report_path = os.path.join("not_processed_failed_report", task_id)
                        if not os.path.exists(failed_report_path):
                            with open(failed_report_path, "w") as _:
                                pass
                except Exception as ee:
                    print(f"Exception reporting error: {ee}")
                    failed_report_path = os.path.join("not_processed_failed_report", task_id)
                    if not os.path.exists(failed_report_path):
                        with open(failed_report_path, "w") as _:
                            pass

                # Cleanup and reset state
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
                    post_data = {"id": task_id, "vtt": vtt_content}
                    post_kwargs = {"json": post_data, "timeout": 10}
                    if auth:
                        post_kwargs["auth"] = auth
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
                if os.path.exists(vtt_file):
                    try:
                        os.rename(vtt_file, os.path.join("processed_uploaded", f"{task_id}.vtt"))
                    except Exception as e:
                        print(f"Failed to move VTT to processed_uploaded for {task_id}: {e}")
            else:
                msg = f"Failed to post result for {task_id} after 3 attempts"
                print(msg)
                log_to_csv(task_id, language, time_taken, current_audio_minutes, "failure", "Failed to post result after 3 attempts")
                send_gotify_error(NODE_NAME, msg)
                if os.path.exists(vtt_file):
                    try:
                        os.rename(vtt_file, os.path.join("processed_not_uploaded", f"{task_id}.vtt"))
                    except Exception as e:
                        print(f"Failed to move VTT to processed_not_uploaded for {task_id}: {e}")
                error_post_data = {"id": task_id}
                error_post_kwargs = {"json": error_post_data, "timeout": 10}
                if auth:
                    error_post_kwargs["auth"] = auth
                try:
                    error_response = requests.post(error_url, **error_post_kwargs)
                    if error_response.status_code == 200:
                        print("Error reported to server due to post failure")
                    else:
                        print(f"Failed to report error: {error_response.status_code}")
                        failed_report_path = os.path.join("not_processed_failed_report", task_id)
                        if not os.path.exists(failed_report_path):
                            with open(failed_report_path, "w") as _:
                                pass
                except Exception as ee:
                    print(f"Exception reporting error: {ee}")
                    failed_report_path = os.path.join("not_processed_failed_report", task_id)
                    if not os.path.exists(failed_report_path):
                        with open(failed_report_path, "w") as _:
                            pass

            # Cleanup remaining files
            cleanup_files([mp3_file, wav_file])
            if os.path.exists(vtt_file):
                try:
                    os.remove(vtt_file)
                except Exception as e:
                    print(f"Failed to remove leftover VTT {vtt_file}: {e}")

            current_task_id = None
            current_language = None
            current_audio_minutes = 0.0
            current_time_taken = 0.0
            current_start_time = None
            current_files = []

            if interrupted:
                break

        except Exception as e:
            console_msg = f"Exception in loop: {e}"
            print(console_msg)
            send_gotify_error(NODE_NAME, console_msg)
            time.sleep(10)
            if interrupted:
                break

# -----------------------------
# CLI and scheduling
# -----------------------------
parser = argparse.ArgumentParser(description="Client handler for task processing")
parser.add_argument("--retry_failed", action="store_true", help="Run in mode to retry failed uploads and error reports")
args = parser.parse_args()

if args.retry_failed:
    retry_failed()
else:
    if not CRON_SCHEDULE:
        process_loop()
    else:
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

            def check_timeout():
                now_local = datetime.now()
                elapsed_seconds = (now_local - session_start).total_seconds()
                return PROCESSING_HOURS > 0 and elapsed_seconds / 3600.0 > PROCESSING_HOURS

            process_loop(check_timeout=check_timeout)

            if interrupted:
                break
