# Dockerfile for distributed_batch_stt client
# Base: Fedora 43
# Supports multiple backends: cpu, vulkan, cuda, openvino

FROM fedora:43

# Build argument for backend selection
ARG BACKEND=cpu
ARG INTEL_OLD_GPU=y
ENV BACKEND=${BACKEND}

# Set working directory
WORKDIR /app

# Install common dependencies
RUN dnf update -y && \
    dnf install -y \
    git cmake gcc gcc-c++ make \
    python3 python3-pip python3-devel \
    wget tar curl \
    zlib-devel bzip2 bzip2-devel readline-devel \
    sqlite sqlite-devel openssl-devel tk-devel \
    libffi-devel xz-devel \
    openblas-devel \
    ffmpeg && \
    dnf clean all

# Install Python packages
RUN pip3 install --no-cache-dir \
    requests \
    croniter \
    python-dotenv

# Backend-specific dependencies
RUN if [ "$BACKEND" = "vulkan" ]; then \
    dnf install -y \
    vulkan-loader vulkan-loader-devel vulkan-headers vulkan-tools \
    mesa-vulkan-drivers glslc vulkan-validation-layers \
    vulkan-validation-layers-devel libshaderc-devel && \
    dnf clean all; \
    elif [ "$BACKEND" = "cuda" ]; then \
    dnf install -y \
    https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-43.noarch.rpm \
    https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-43.noarch.rpm && \
    dnf install -y cuda cuda-devel && \
    dnf clean all; \
    elif [ "$BACKEND" = "openvino" ]; then \
    dnf install -y pugixml-devel pugixml tbb intel-opencl clinfo && \
    dnf clean all; \
    fi

# Copy client code
COPY client.py /app/
COPY whisper/ /app/whisper/

# Create necessary directories
RUN mkdir -p processed_uploaded processed_not_uploaded not_processed_failed_report

# Build whisper.cpp based on backend
WORKDIR /app/whisper
RUN MODEL="medium" && \
    if [ ! -d "whisper.cpp" ]; then \
    git clone https://github.com/ggerganov/whisper.cpp.git; \
    fi && \
    cd whisper.cpp && \
    # If Vulkan and Intel GPU 13th gen or older, disable flash attention
    if [ "$BACKEND" = "vulkan" ]; then \
    INTEL_OLD_GPU=${INTEL_OLD_GPU:-y}; \
    if [ "$INTEL_OLD_GPU" = "y" ]; then \
    echo "Disabling flash attention for Intel Xe stability..."; \
    sed -i 's/bool\s\+flash_attn\s*=\s*true;/bool flash_attn = false;/g' examples/cli/cli.cpp; \
    git add examples/cli/cli.cpp; \
    git commit -m "Disable flash attention for Intel Xe stability" || echo "No changes to commit (already patched)."; \
    fi; \
    fi && \
    # Download VAD model (optional)
    bash ./models/download-vad-model.sh silero-v5.1.2 || echo "VAD optional - skipped" && \
    # Download GGML model
    bash ./models/download-ggml-model.sh $MODEL && \
    # Set build flags based on backend
    if [ "$BACKEND" = "vulkan" ]; then \
    BUILD_FLAGS="-DGGML_VULKAN=1"; \
    elif [ "$BACKEND" = "cuda" ]; then \
    BUILD_FLAGS="-DGGML_CUDA=1 -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"; \
    elif [ "$BACKEND" = "openvino" ]; then \
    BUILD_FLAGS="-DWHISPER_OPENVINO=1 -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"; \
    else \
    BUILD_FLAGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS"; \
    fi && \
    # Build
    cmake -B build $BUILD_FLAGS && \
    cmake --build build -j$(nproc) --config Release

# For OpenVINO, additional setup
RUN if [ "$BACKEND" = "openvino" ]; then \
    cd /app/whisper/whisper.cpp/models && \
    python3 -m venv openvino_conv_env && \
    . openvino_conv_env/bin/activate && \
    python3 -m pip install --upgrade pip setuptools && \
    python3 -m pip install numpy torch openai-whisper croniter && \
    python3 -m pip install -r requirements-openvino.txt && \
    python3 convert-whisper-to-openvino.py --model medium && \
    deactivate && \
    cd /app/whisper/whisper.cpp && \
    wget https://storage.openvinotoolkit.org/repositories/openvino/packages/2025.2/linux/openvino_toolkit_rhel8_2025.2.0.19140.c01cd93e24d_x86_64.tgz && \
    tar -xzf openvino_toolkit_rhel8_2025.2.0.19140.c01cd93e24d_x86_64.tgz && \
    rm openvino_toolkit_rhel8_2025.2.0.19140.c01cd93e24d_x86_64.tgz; \
    fi

# Set working directory back to /app
WORKDIR /app

# Expose any ports if needed (none for this client)

# Default command
CMD ["python3", "client.py"]
