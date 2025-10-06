
# Distributed Batch Speech-to-Text (STT) System

This project provides a distributed batch audio transcription system using [whisper.cpp](https://github.com/ggerganov/whisper.cpp) for fast, local speech-to-text. It consists of a **client** (audio processor/uploader) and a **server** (task distributor/collector). The system is designed for Linux (Fedora recommended), but can be adapted for Mac and Windows.

---

## 1. Client Setup

The client downloads audio tasks from the server, transcribes them using whisper.cpp, and uploads the results. It is intended to run on Linux (Fedora), but can be used on Mac and Windows with some manual steps.

### Requirements
- **Python 3.8+**
- **ffmpeg** (must be installed and available in your system PATH)
- **requests** Python package (`pip install requests`)
- **whisper.cpp** binary (see below)

### Configuration
Copy `.env.example` to `.env` and edit the values for your server and credentials:

```sh
cp client/.env.example client/.env
# Edit client/.env as needed
```

You can load the environment variables with:

```sh
source client/load_env.sh
```

---

### A. Linux (Fedora recommended)

#### **Recommended: Use Fedora via Distrobox (if not on Fedora)**
If you are not running Fedora, you can use [Distrobox](https://distrobox.it/) to create a Fedora container for a clean build environment.

#### **Automatic Setup (Fedora)**
Run the provided script to install dependencies and build whisper.cpp:

```sh
cd client/whisper
chmod +x build-linux_fedora.sh
./build-linux_fedora.sh
```
Follow the prompts to select your backend (default: CPU). The script will build whisper.cpp and download the required model.

The resulting binary will be at:
```
client/whisper/whisper.cpp/build/bin/whisper-cli
```

#### **Manual Setup**
If you prefer, follow the [whisper.cpp instructions](https://github.com/ggerganov/whisper.cpp) to build manually.

---

### B. Mac

1. Follow instructions in more detail: [whisper.cpp](https://github.com/ggml-org/whisper.cpp).
```sh
# Apple Silicon
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp
cmake -B build
cmake --build build --config Release
```
You might need to have Xcode and cmake 
```sh 
brew install cmake 
```
2. Place the binary as `client/whisper/whisper.cpp/build/bin/whisper-cli` (create the folder if needed).
3. Ensure `ffmpeg` is installed (e.g., via Homebrew: `brew install ffmpeg`).

---

### C. Windows

1. Download the latest whisper.cpp binary for Windows from [whisper.cpp releases](https://github.com/ggml-org/whisper.cpp/releases).
2. Place the binary as `client/whisper/whisper.cpp/build/bin/whisper-cli` (create the folder if needed).
3. Ensure `ffmpeg` is installed and available in your system PATH.

---

### Running the Client

After building or downloading the binary and setting up your `.env`, run:

```sh
python client/client.py
```

The client will poll the server for tasks, process them, and upload results. If authentication is enabled, set `AUTH_ENABLED=true` and provide `USERNAME` and `PASSWORD` in your `.env`.

---

## 2. Server Setup

The server is a FastAPI app that distributes audio tasks and collects results. It is designed to run in a Docker container for easy deployment and persistence.

### Build and Run with Docker

1. **Build the Docker image:**
	```sh
	cd server
	docker build -t whisper-server .
	# or with Podman:
	# podman build -t whisper-server .
	```

2. **Run the container, mapping volumes for persistence:**
	```sh
	docker run -d \
	  -p 8000:8000 \
	  -v /path/to/logs:/app/logs:Z \
	  -v /path/to/inprogress.txt:/app/inprogress.txt:Z \
	  -v /mnt/data/video:/mnt/data/video:Z \
	  whisper-server
	```
	- Replace `/mnt/data/video` with the actual path to your MP3 files.
	- Map `logs` and `inprogress.txt` to host locations to avoid data loss when rebuilding the container.

3. **Environment Variables:**
	- Copy `.env.example` to `.env` and edit as needed:
	  ```sh
	  cp server/.env.example server/.env
	  # Edit server/.env
	  ```
	- Set `AUDIO_DIR` to the directory containing your MP3 files.

4. **Access the API:**
	- The server listens on port 8000 by default.
	- Endpoints:
	  - `GET /task` – Get a new audio task
	  - `POST /result` – Submit a completed transcription
	  - `POST /error` – Report a failed task

### Authentication

**Note:** The server does **not** implement authentication itself. It is recommended to run the server behind a reverse proxy (e.g., Caddy, Nginx) that enforces BASIC AUTH or other authentication at the proxy level. Failing to do so exposes the server to accepting any gibberish files.

---

## 3. Additional Notes

- **ffmpeg** must be installed and available in your system PATH for the client to convert audio files.
- The client and server communicate using HTTP. Ensure network connectivity between them.
- For GPU acceleration, build whisper.cpp with the appropriate backend (CUDA, Vulkan, OpenVINO). See the build script for options.
- For troubleshooting, check the logs in the mapped `logs` directory on the server.

---

## 4. References

- [whisper.cpp GitHub](https://github.com/ggerganov/whisper.cpp)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Distrobox](https://distrobox.it/)