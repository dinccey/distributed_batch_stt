#!/bin/bash

# Bash script for Fedora 42 to set up environment, build whisper.cpp with selected backend support,
# and provide an example for creating .vtt subtitles with specified language.
# Default backend is CPU only. Supports Vulkan, NVIDIA (CUDA), and OpenVINO.
# Uses pyenv for Python 3.10 only if OpenVINO is selected.

set -e  # Exit on error

# Define model variable
MODEL="medium"

echo "Starting whisper.cpp setup for model: $MODEL..."

# Ask for backend
read -p "Which backend to build for? (cpu, vulkan, cuda, openvino) [cpu]: " backend
backend=${backend:-cpu}

# Step 1: Install common system dependencies
echo "Installing common system dependencies..."
sudo dnf update -y
sudo dnf install -y git cmake gcc gcc-c++ make python3 python3-pip python3-devel wget tar \
    zlib-devel bzip2 bzip2-devel readline-devel sqlite sqlite-devel openssl-devel tk-devel \
    libffi-devel xz-devel curl

# Backend-specific dependencies and flags
case $backend in
    cpu)
        echo "Building for CPU backend (default)."
        build_flags=""
        ;;
    vulkan)
        echo "Building for Vulkan backend."
        sudo dnf install -y vulkan-loader vulkan-loader-devel vulkan-tools mesa-vulkan-drivers
        build_flags="-DGGML_VULKAN=1"
        ;;
    cuda)
        echo "Building for CUDA (NVIDIA) backend."
        # Enable RPM Fusion if not already
        if ! rpm -q rpmfusion-free-release >/dev/null 2>&1; then
            echo "Enabling RPM Fusion..."
            sudo dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
            sudo dnf install -y https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
        fi
        sudo dnf install -y cuda cuda-devel
        build_flags="-DGGML_CUDA=1"
        ;;
    openvino)
        echo "Building for OpenVINO backend."
        # Additional dependencies for OpenVINO
        sudo dnf install -y pugixml-devel pugixml tbb
        sudo dnf install -y intel-opencl
        sudo dnf install -y clinfo
        build_flags="-DWHISPER_OPENVINO=1"
        ;;
    *)
        echo "Invalid backend: $backend. Exiting."
        exit 1
        ;;
esac

# Step 2: Clone whisper.cpp repository if not exists
if [ ! -d "whisper.cpp" ]; then
    echo "Cloning whisper.cpp repository..."
    git clone https://github.com/ggerganov/whisper.cpp.git
fi
cd whisper.cpp

# Step 3: Download GGML model
echo "Downloading GGML $MODEL model..."
bash ./models/download-ggml-model.sh $MODEL

if [ "$backend" = "openvino" ]; then
    # Step 4: Install pyenv for Python 3.10
    echo "Installing pyenv..."
    curl https://pyenv.run | bash

    # Set up pyenv environment variables
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init --path)"
    eval "$(pyenv init -)"

    # Step 5: Install Python 3.10 using pyenv
    echo "Installing Python 3.10 with pyenv..."
    pyenv install 3.10.15 -s  # Use a specific 3.10 version; -s skips if already installed
    pyenv local 3.10.15  # Set local to avoid global change

    # Step 6: Set up Python virtual environment with Python 3.10
    echo "Setting up Python virtual environment with Python 3.10..."
    cd models
    python3.10 -m venv openvino_conv_env
    source openvino_conv_env/bin/activate
    python3.10 -m pip install --upgrade pip
    python3.10 -m pip install --upgrade setuptools
    python3.10 -m pip install numpy torch openai-whisper
    python3.10 -m pip install -r requirements-openvino.txt

    # Step 7: Check for conflicting whisper.py file
    echo "Checking for conflicting whisper.py file..."
    if [ -f "openvino_conv_env/lib/python3.10/site-packages/whisper.py" ]; then
        echo "Removing conflicting whisper.py file..."
        rm -f openvino_conv_env/lib/python3.10/site-packages/whisper.py
    fi

    # Step 8: Generate OpenVINO encoder model
    echo "Generating OpenVINO model for $MODEL..."
    python3.10 convert-whisper-to-openvino.py --model $MODEL

    # Deactivate virtual environment
    deactivate
    cd ..

    # Step 9: Download and extract OpenVINO toolkit
    echo "Downloading OpenVINO toolkit..."
    OV_TGZ="openvino_toolkit_rhel8_2025.2.0.19140.c01cd93e24d_x86_64.tgz"
    wget https://storage.openvinotoolkit.org/repositories/openvino/packages/2025.2/linux/$OV_TGZ
    tar -xzf $OV_TGZ
    OV_DIR="openvino_toolkit_rhel8_2025.2.0.19140.c01cd93e24d_x86_64"
    echo "Sourcing OpenVINO environment..."
    source $OV_DIR/setupvars.sh

    # Add OpenVINO dir to build flags
    build_flags="$build_flags -DOpenVINO_DIR=./$OV_DIR/runtime/cmake"
fi

# Step 10: Build whisper.cpp with selected backend
echo "Building whisper.cpp with selected backend..."
cmake -B build $build_flags
cmake --build build -j$(nproc) --config Release

# Step 11: Print completion message and example usage
echo -e "\nBuild completed successfully!"

device_option=""
if [ "$backend" = "openvino" ]; then
    device_option="-oved GPU"
fi
# For other backends, device is automatically selected if built with support

echo "Example usage for creating .vtt subtitles with English language:"
echo "./build/bin/main -m ./models/ggml-${MODEL}.bin -f example.wav --output-vtt -of subtitles -l en $device_option"

echo "For server:"
echo "./build/bin/server -m ./models/ggml-${MODEL}.bin $device_option"