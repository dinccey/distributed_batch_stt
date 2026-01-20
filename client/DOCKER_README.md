# Docker Setup for Client

This directory contains Docker setup for the distributed batch STT client.

## Files

- **Dockerfile** - Multi-backend Dockerfile based on Fedora 43
- **build.sh** - Interactive script to build the Docker image
- **run.sh** - Interactive script to run/redeploy the container
- **client.py** - Main client application
- **.env** - Environment configuration (you need to create this)

## Quick Start

### 1. Create your .env file

Create a `.env` file in the client directory with your configuration:

```bash
# Server configuration
SERVER_URL=http://your-server:8000
AUTH_ENABLED=false
USERNAME=your_username
PASSWORD=your_password

# Processing configuration
CRON=0 */6 * * *
PROCESSING_HOURS=1

# VAD configuration
VAD_ENABLED=true
VAD_MODEL=./whisper/whisper.cpp/models/ggml-silero-v5.1.2.bin

# Gotify notifications (optional)
NODE_NAME=MyNode
GOTIFY_URL=https://gotify.example.com
GOTIFY_KEY=your_gotify_key
```

### 2. Build the Docker image

Run the build script and select your backend:

```bash
./build.sh
```

You'll be prompted to select:
1. **cpu** - CPU backend (OpenBLAS accelerated) - works everywhere
2. **vulkan** - Vulkan backend for AMD/Intel/NVIDIA GPUs
3. **cuda** - CUDA backend for NVIDIA GPUs
4. **openvino** - OpenVINO backend for Intel hardware

The script will build a Docker image with the selected backend.

### 3. Run the container

Run the deployment script:

```bash
./run.sh
```

You'll be prompted to select:
- Container runtime (docker or podman)
- Backend to run (must match a previously built image)

The script will:
- Stop and remove any existing container
- Start a new container with the selected configuration
- Mount persistent volumes for processed files
- Pass your .env configuration to the container

## Backend Details

### CPU Backend
- Works on any system
- Uses OpenBLAS for acceleration
- Slowest option but most compatible

### Vulkan Backend
- Works with AMD, Intel, and NVIDIA GPUs
- Requires `/dev/dri` device access
- First run compiles shaders (10-40 seconds delay)
- Fastest option for compatible GPUs

### CUDA Backend
- NVIDIA GPUs only
- Requires NVIDIA Docker runtime (docker) or proper GPU passthrough (podman)
- Very fast on NVIDIA hardware

### OpenVINO Backend
- Optimized for Intel CPUs and GPUs
- Requires `/dev/dri` device access for GPU acceleration

## Persistent Data

The following directories are mounted as volumes:
- `processed_uploaded/` - Successfully uploaded VTT files
- `processed_not_uploaded/` - Processed but not yet uploaded files
- `not_processed_failed_report/` - Failed processing reports
- `processed.csv` - Processing log

## Useful Commands

View container logs:
```bash
docker logs -f distributed-batch-stt-client-cpu
# or
podman logs -f distributed-batch-stt-client-cpu
```

Stop the container:
```bash
docker stop distributed-batch-stt-client-cpu
# or
podman stop distributed-batch-stt-client-cpu
```

Restart the container:
```bash
docker start distributed-batch-stt-client-cpu
# or
podman start distributed-batch-stt-client-cpu
```

Remove the container:
```bash
docker rm -f distributed-batch-stt-client-cpu
# or
podman rm -f distributed-batch-stt-client-cpu
```

## Troubleshooting

### Build fails
- Make sure you have sufficient disk space (whisper.cpp models are large)
- Check that you have internet connectivity for package downloads
- For CUDA backend, ensure RPM Fusion repos are accessible

### Container won't start
- Check that the .env file exists and is properly formatted
- Verify the image was built successfully: `docker images | grep distributed-batch-stt-client`
- Check logs: `docker logs distributed-batch-stt-client-<backend>`

### GPU not detected (Vulkan/CUDA/OpenVINO)
- Verify GPU drivers are installed on host
- For Vulkan: Check `ls -la /dev/dri` shows devices
- For CUDA with Docker: Ensure nvidia-docker2 is installed
- For CUDA with Podman: Check CDI configuration

### Performance issues
- CPU backend is slow by design - consider GPU backends
- Vulkan first run is slow (shader compilation) - subsequent runs are fast
- Check system resources: `docker stats distributed-batch-stt-client-<backend>`

## Rebuilding

To rebuild with a different backend or updated code:

1. Build new image: `./build.sh`
2. Redeploy container: `./run.sh`

The run script automatically stops and removes the old container before starting the new one.
