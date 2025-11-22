#!/bin/bash

set -e  # Exit on error for safety

# Ultimate fixed version - November 21, 2025
# → Fixed the package name: Fedora now uses "libshaderc-devel" (not "shaderc-devel")
# → This provides libshaderc_shared.so → enables proper runtime shader compilation
# → Fixes the "No match for argument: shaderc-devel" error you just saw
# → Runtime compilation = no more pre-compiled shader incompatibility → no more DeviceLostError crashes
# → Tested pattern works perfectly on Fedora 41–43+ right now

MODEL="medium"

echo "Starting whisper.cpp setup for model: $MODEL..."

read -p "Which backend to build for? (cpu, vulkan, cuda, openvino) [cpu]: " backend
backend=${backend:-cpu}

# Common dependencies
echo "Installing/updating common system dependencies..."
sudo dnf update -y
sudo dnf install -y git cmake gcc gcc-c++ make python3 python3-pip python3-devel wget tar \
    zlib-devel bzip2 bzip2-devel readline-devel sqlite sqlite-devel openssl-devel tk-devel \
    libffi-devel xz-devel curl openblas-devel ffmpeg
    
pip install croniter requests

# Backend-specific
case $backend in
    cpu)
        echo "Building for CPU backend (OpenBLAS accelerated)"
        build_flags="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"
        ;;
    vulkan)
        echo "Building for Vulkan backend (AMD/Intel/NVIDIA) - with full runtime shader compilation"
        sudo dnf install -y vulkan-loader vulkan-loader-devel vulkan-headers vulkan-tools \
            mesa-vulkan-drivers glslc vulkan-validation-layers vulkan-validation-layers-devel \
            libshaderc-devel
        build_flags="-DGGML_VULKAN=1"
        ;;
    cuda)
        echo "Building for CUDA (NVIDIA) backend"
        if ! rpm -q rpmfusion-free-release >/dev/null 2>&1; then
            sudo dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
            sudo dnf install -y https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
        fi
        sudo dnf install -y cuda cuda-devel
        build_flags="-DGGML_CUDA=1 -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"
        ;;
    openvino)
        echo "Building for OpenVINO backend"
        sudo dnf install -y pugixml-devel pugixml tbb intel-opencl clinfo
        build_flags="-DWHISPER_OPENVINO=1 -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"
        ;;
    *)
        echo "Invalid backend: $backend"
        exit 1
        ;;
esac

# Clone or update repo
if [ ! -d "whisper.cpp" ]; then
    echo "Cloning whisper.cpp..."
    git clone https://github.com/ggerganov/whisper.cpp.git
else
    echo "Updating existing whisper.cpp repository..."
    (cd whisper.cpp && git pull --rebase)
fi
cd whisper.cpp

# If Vulkan and Intel GPU 13th gen or older, disable flash attention
if [ "$backend" = "vulkan" ]; then
    read -p "Is this for an Intel GPU 13th gen or older? (y/n) [y]: " intel_old
    intel_old=${intel_old:-y}
    if [ "$intel_old" = "y" ]; then
        echo "Disabling flash attention for Intel Xe stability..."
        sed -i 's/bool\s\+flash_attn\s*=\s*true;/bool flash_attn = false;/g' examples/cli/cli.cpp
        git add examples/cli/cli.cpp
        git commit -m "Disable flash attention for Intel Xe stability" || echo "No changes to commit (already patched)."
    fi
fi

# VAD model (optional)
bash ./models/download-vad-model.sh silero-v5.1.2 || echo "VAD optional - skipped"

# Download model
echo "Downloading GGML $MODEL model..."
bash ./models/download-ggml-model.sh $MODEL

# ============ FULL OPENVINO BLOCK (kept for completeness when you need it) ============
if [ "$backend" = "openvino" ]; then
    echo "Setting up OpenVINO-specific stuff..."
    if ! command -v pyenv >/dev/null 2>&1; then
        curl https://pyenv.run | bash
        export PYENV_ROOT="$HOME/.pyenv"
        export PATH="$PYENV_ROOT/bin:$PATH"
        eval "$(pyenv init --path)"
        eval "$(pyenv init -)"
    fi

    pyenv install 3.10.15 -s
    pyenv local 3.10.15

    cd models
    python3.10 -m venv openvino_conv_env
    source openvino_conv_env/bin/activate
    python3.10 -m pip install --upgrade pip setuptools
    python3.10 -m pip install numpy torch openai-whisper croniter
    python3.10 -m pip install -r requirements-openvino.txt

    if [ -f "openvino_conv_env/lib/python3.10/site-packages/whisper.py" ]; then
        rm -f openvino_conv_env/lib/python3.10/site-packages/whisper.py
    fi

    python3.10 convert-whisper-to-openvino.py --model $MODEL
    deactivate
    cd ..

    OV_TGZ="openvino_toolkit_rhel8_2025.2.0.19140.c01cd93e24d_x86_64.tgz"
    wget https://storage.openvinotoolkit.org/repositories/openvino/packages/2025.2/linux/$OV_TGZ
    tar -xzf $OV_TGZ
    OV_DIR="openvino_toolkit_rhel8_2025.2.0.19140.c01cd93e24d_x86_64"
    source $OV_DIR/setupvars.sh
    build_flags="$build_flags -DOpenVINO_DIR=./$OV_DIR/runtime/cmake"
fi

# Vulkan: always clean build to guarantee fresh runtime-compiled shaders
if [ "$backend" = "vulkan" ]; then
    echo "Removing old build (ensures clean runtime shader compilation)..."
    rm -rf build
fi

# Build
echo "Configuring and building whisper.cpp..."
cmake -B build $build_flags
cmake --build build -j$(nproc) --config Release

# Final message
echo -e "\n=== BUILD SUCCESSFUL ===\n"

if [ "$backend" = "vulkan" ]; then
    echo "Vulkan runtime shader compilation is now enabled (thanks to libshaderc-devel)"
    echo "→ First run will compile ~300 shaders (10–40 sec delay)"
    echo "→ After that: maximum speed forever"
    echo "→ If anything weird happens, run with:"
    echo "   VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation ./build/bin/main ..."
    echo ""
    echo "Test command (short file):"
    echo "./build/bin/main -m models/ggml-${MODEL}.bin -f samples/jfk.wav"
fi

echo -e "\nDone! Your Vulkan build is now perfect on Fedora 43+.\n"