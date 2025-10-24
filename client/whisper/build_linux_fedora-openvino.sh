#!/bin/bash

# Bash script for Fedora 42 to set up environment, build whisper.cpp with OpenVINO support,
# and provide an example for creating .vtt subtitles with specified language on GPU.
# Uses pyenv to manage Python 3.10.

#set -e  # Exit on error

# Define model variable
MODEL="medium"

echo "Starting whisper.cpp setup with OpenVINO support for model: $MODEL..."

# Step 1: Install system dependencies
echo "Installing system dependencies..."
sudo dnf update -y
sudo dnf install -y git cmake gcc gcc-c++ make python3 python3-pip python3-devel wget tar \
    zlib-devel bzip2 bzip2-devel readline-devel sqlite sqlite-devel openssl-devel tk-devel \
    libffi-devel xz-devel curl

#https://github.com/ggml-org/whisper.cpp/issues/3105
sudo dnf install -y pugixml-devel pugixml tbb
sudo dnf install -y intel-opencl
sudo dnf install clinfo

# Step 2: Install pyenv
echo "Installing pyenv..."
curl https://pyenv.run | bash

# Set up pyenv environment variables
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init --path)"
eval "$(pyenv init -)"

# Step 3: Install Python 3.10 using pyenv
echo "Installing Python 3.10 with pyenv..."
pyenv install 3.10.15 -s  # Use a specific 3.10 version; -s skips if already installed
pyenv global 3.10.15

# Step 4: Clone whisper.cpp repository
echo "Cloning whisper.cpp repository..."
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp

# Step 5: Download GGML model
echo "Downloading GGML $MODEL model..."
bash ./models/download-ggml-model.sh $MODEL

# Download VAD model
bash ./models/download-vad-model.sh silero-v5.1.2

# Step 6: Set up Python virtual environment with Python 3.10
echo "Setting up Python virtual environment with Python 3.10..."
cd models
python3.10 -m venv openvino_conv_env
source openvino_conv_env/bin/activate
python3.10 -m pip install --upgrade pip
python3.10 -m pip install --upgrade setuptools
python3.10 -m pip install numpy torch openai-whisper croniter


# Step 7: Check for conflicting whisper.py file
echo "Checking for conflicting whisper.py file..."
if [ -f "openvino_conv_env/lib/python3.10/site-packages/whisper.py" ]; then
    echo "Removing conflicpython3.10 -m pip install -r requirements-openvino.txtting whisper.py file..."
    rm -f openvino_conv_env/lib/python3.10/site-packages/whisper.py
fi

# Step 8: Generate OpenVINO encoder model
echo "Generating OpenVINO model for $MODEL..."
python3.10 convert-whisper-to-openvino.py --model $MODEL

# Deactivate virtual environment
deactivate
cd ..
pwd
# Step 9: Download and extract OpenVINO toolkit
echo "Downloading OpenVINO toolkit..."
OV_TGZ="openvino_toolkit_rhel8_2025.3.0.19807.44526285f24_x86_64.tgz"
wget https://storage.openvinotoolkit.org/repositories/openvino/packages/2025.3/linux/$OV_TGZ
tar -xzf $OV_TGZ
OV_DIR="openvino_toolkit_rhel8_2025.3.0.19807.44526285f24_x86_64"
echo "Sourcing OpenVINO environment..."
source $OV_DIR/setupvars.sh

# Step 10: Build whisper.cpp with OpenVINO support
echo "Building whisper.cpp with OpenVINO support..."
cmake -B build -DWHISPER_OPENVINO=1 -DOpenVINO_DIR="./$OV_DIR/runtime/cmake"
cmake --build build -j$(nproc) --config Release

# Step 11: Print completion message and example usage
echo -e "\nBuild completed successfully!"
echo "Example usage for creating .vtt subtitles with English language on GPU:"
echo "./whisper.cpp/build/bin/whisper-cli -m ./whisper.cpp/models/ggml-medium.bin -f example.mp3 --output-vtt -of subtitles -l en -oved GPU"
echo "./whisper.cpp/build/bin/whisper-server -m ./whisper.cpp/models/ggml-medium.bin -oved GPU"
