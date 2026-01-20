#!/bin/bash

# build.sh - Helper script to build the client Docker image
# This script asks the user for the backend architecture and builds accordingly

set -e

echo "======================================"
echo "  Client Docker Image Builder"
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

# Ask for backend selection
echo "Select the backend architecture to build for:"
echo "  1) cpu      - CPU backend (OpenBLAS accelerated)"
echo "  2) vulkan   - Vulkan backend (AMD/Intel/NVIDIA GPUs)"
echo "  3) cuda     - CUDA backend (NVIDIA GPUs)"
echo "  4) openvino - OpenVINO backend (Intel hardware)"
echo ""

read -p "Enter your choice (1-4) [1]: " choice
choice=${choice:-1}

case $choice in
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

# If Vulkan, ask about Intel GPU
INTEL_OLD_GPU="n"
if [ "$BACKEND" = "vulkan" ]; then
    echo ""
    read -p "Is this for an Intel GPU 13th gen or older? (y/n) [y]: " INTEL_OLD_GPU
    INTEL_OLD_GPU=${INTEL_OLD_GPU:-y}
fi

echo ""
echo "Building Docker image for backend: $BACKEND"
echo ""

# Image name and tag
IMAGE_NAME="distributed-batch-stt-client"
IMAGE_TAG="${BACKEND}-$(date +%Y%m%d-%H%M%S)"
IMAGE_FULL="${IMAGE_NAME}:${IMAGE_TAG}"
IMAGE_LATEST="${IMAGE_NAME}:${BACKEND}-latest"

# Build the Docker image
echo "Building: $IMAGE_FULL"
echo ""

$RUNTIME build \
    --build-arg BACKEND=$BACKEND \
    --build-arg INTEL_OLD_GPU=$INTEL_OLD_GPU \
    -t $IMAGE_FULL \
    -t $IMAGE_LATEST \
    -f Dockerfile \
    .

echo ""
echo "======================================"
echo "  Build Complete!"
echo "======================================"
echo "Runtime: $RUNTIME"
echo "Image tags:"
echo "  - $IMAGE_FULL"
echo "  - $IMAGE_LATEST"
echo ""
echo "To run the container, use the run.sh script."
echo ""
