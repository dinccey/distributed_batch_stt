#!/bin/bash

# run.sh - Helper script to run/redeploy the client container
# This script asks for container runtime (docker/podman) and manages the container

set -e

echo "======================================"
echo "  Client Container Runner"
echo "======================================"
echo ""

# Ask for container runtime
echo "Select the container runtime:"
echo "  1) docker"
echo "  2) podman"
echo ""

read -p "Enter your choice (1-2) [1]: " runtime_choice
runtime_choice=${runtime_choice:-1}

case $runtime_choice in
    1)
        RUNTIME="docker"
        ;;
    2)
        RUNTIME="podman"
        ;;
    *)
        echo "Invalid choice. Defaulting to docker."
        RUNTIME="docker"
        ;;
esac

echo ""
echo "Using container runtime: $RUNTIME"
echo ""

# Ask for backend to use
echo "Select the backend to run (must match a built image):"
echo "  1) cpu"
echo "  2) vulkan"
echo "  3) cuda"
echo "  4) openvino"
echo ""

read -p "Enter your choice (1-4) [1]: " backend_choice
backend_choice=${backend_choice:-1}

case $backend_choice in
    1)
        BACKEND="cpu"
        ;;
    2)
        BACKEND="vulkan"
        ;;
    3)
        BACKEND="cuda"
        ;;
    4)
        BACKEND="openvino"
        ;;
    *)
        echo "Invalid choice. Defaulting to CPU."
        BACKEND="cpu"
        ;;
esac

# Container and image names
CONTAINER_NAME="distributed-batch-stt-client-${BACKEND}"
IMAGE_NAME="distributed-batch-stt-client:${BACKEND}-latest"

echo ""
echo "Container name: $CONTAINER_NAME"
echo "Image: $IMAGE_NAME"
echo ""

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "Warning: .env file not found in current directory."
    read -p "Do you want to continue anyway? (y/n) [n]: " continue_choice
    continue_choice=${continue_choice:-n}
    if [ "$continue_choice" != "y" ]; then
        echo "Exiting. Please create a .env file first."
        exit 1
    fi
fi

# Stop and remove existing container if it exists
echo "Checking for existing container..."
if $RUNTIME ps -a --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
    echo "Stopping and removing existing container: $CONTAINER_NAME"
    $RUNTIME stop $CONTAINER_NAME 2>/dev/null || true
    $RUNTIME rm $CONTAINER_NAME 2>/dev/null || true
fi

# Ensure directories exist and processed.csv is a file with headers
echo "Ensuring persistent data directories exist..."
mkdir -p processed_uploaded processed_not_uploaded not_processed_failed_report
if [ ! -f "processed.csv" ]; then
    echo "file_id,language,time_taken,audio_minutes,status,reason" > processed.csv
fi
# Ensure proper permissions for container access
chmod 666 processed.csv
chmod 777 processed_uploaded processed_not_uploaded not_processed_failed_report

# Additional runtime arguments based on backend
RUNTIME_ARGS=""

if [ "$BACKEND" = "vulkan" ]; then
    echo "Configuring Vulkan support..."
    RUNTIME_ARGS="--device /dev/dri"
elif [ "$BACKEND" = "cuda" ]; then
    echo "Configuring CUDA support..."
    if [ "$RUNTIME" = "docker" ]; then
        RUNTIME_ARGS="--gpus all"
    else
        # Podman needs different flags for GPU
        RUNTIME_ARGS="--device nvidia.com/gpu=all --security-opt=label=disable"
    fi
elif [ "$BACKEND" = "openvino" ]; then
    echo "Configuring OpenVINO support..."
    RUNTIME_ARGS="--device /dev/dri"
fi

# Volume mounts for persistent data
VOLUME_ARGS="-v $(pwd)/processed_uploaded:/app/processed_uploaded:z"
VOLUME_ARGS="$VOLUME_ARGS -v $(pwd)/processed_not_uploaded:/app/processed_not_uploaded:z"
VOLUME_ARGS="$VOLUME_ARGS -v $(pwd)/not_processed_failed_report:/app/not_processed_failed_report:z"
VOLUME_ARGS="$VOLUME_ARGS -v $(pwd)/processed.csv:/app/processed.csv:z"

# Pass .env file if it exists
if [ -f ".env" ]; then
    VOLUME_ARGS="$VOLUME_ARGS --env-file .env"
fi

echo ""
echo "Starting container..."
echo ""

# Run the container
$RUNTIME run -d \
    --name $CONTAINER_NAME \
    --restart unless-stopped \
    $RUNTIME_ARGS \
    $VOLUME_ARGS \
    $IMAGE_NAME

echo ""
echo "======================================"
echo "  Container Started!"
echo "======================================"
echo "Container name: $CONTAINER_NAME"
echo "Backend: $BACKEND"
echo "Runtime: $RUNTIME"
echo ""
echo "Useful commands:"
echo "  View logs:       $RUNTIME logs -f $CONTAINER_NAME"
echo "  Stop container:  $RUNTIME stop $CONTAINER_NAME"
echo "  Start container: $RUNTIME start $CONTAINER_NAME"
echo "  Remove container: $RUNTIME rm -f $CONTAINER_NAME"
echo ""
echo "To view logs now, run:"
echo "  $RUNTIME logs -f $CONTAINER_NAME"
echo ""
