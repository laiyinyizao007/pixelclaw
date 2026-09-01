#!/bin/bash
#
# PixelClaw Setup Script
# Installs dependencies and configures the environment for Raspberry Pi 5 (aarch64)
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
PYTHON_VERSION="3.9"

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                PixelClaw Setup Script                        ║"
echo "║         OpenClaw Automation for Raspberry Pi 5               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo -e "${BLUE}Project directory: $PROJECT_DIR${NC}\n"

# Function to print status
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running on Raspberry Pi
check_platform() {
    print_status "Checking platform..."

    ARCH=$(uname -m)
    if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
        print_success "Running on ARM64 architecture ($ARCH)"
    else
        print_warning "Not running on ARM64 ($ARCH). Some optimizations may not apply."
    fi

    # Check for Raspberry Pi
    if [[ -f /proc/device-tree/model ]]; then
        MODEL=$(cat /proc/device-tree/model)
        print_success "Platform: $MODEL"
    fi
}

# Install system dependencies
install_system_deps() {
    print_status "Installing system dependencies..."

    sudo apt-get update

    # Core dependencies
    sudo apt-get install -y \
        python3 \
        python3-pip \
        python3-venv \
        python3-dev \
        libgl1-mesa-glx \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
        wget \
        curl \
        git \
        adb

    # Optional: For building OpenCV
    sudo apt-get install -y \
        libjpeg-dev \
        libpng-dev \
        libtiff-dev \
        libavcodec-dev \
        libavformat-dev \
        libswscale-dev \
        libv4l-dev \
        libxvidcore-dev \
        libx264-dev \
        libgtk-3-dev \
        libatlas-base-dev \
        gfortran

    print_success "System dependencies installed"
}

# Install Android SDK Platform Tools
install_android_sdk() {
    print_status "Checking Android SDK Platform Tools..."

    if command -v adb &> /dev/null; then
        ADB_VERSION=$(adb version | head -n 1)
        print_success "ADB already installed: $ADB_VERSION"
    else
        print_status "Installing Android SDK Platform Tools..."

        PLATFORM_TOOLS_URL="https://dl.google.com/android/repository/platform-tools-latest-linux.zip"
        TEMP_DIR=$(mktemp -d)

        cd "$TEMP_DIR"
        wget -q "$PLATFORM_TOOLS_URL" -O platform-tools.zip
        unzip -q platform-tools.zip

        sudo mkdir -p /opt/android-sdk
        sudo mv platform-tools /opt/android-sdk/
        sudo ln -sf /opt/android-sdk/platform-tools/adb /usr/local/bin/adb
        sudo ln -sf /opt/android-sdk/platform-tools/fastboot /usr/local/bin/fastboot

        cd "$PROJECT_DIR"
        rm -rf "$TEMP_DIR"

        print_success "Android SDK Platform Tools installed"
    fi

    # Add to PATH if not already there
    if ! grep -q "/opt/android-sdk/platform-tools" ~/.bashrc; then
        echo 'export PATH=$PATH:/opt/android-sdk/platform-tools' >> ~/.bashrc
        print_status "Added Android SDK to PATH in ~/.bashrc"
    fi
}

# Create Python virtual environment
setup_python_env() {
    print_status "Setting up Python virtual environment..."

    cd "$PROJECT_DIR"

    if [[ -d "$VENV_DIR" ]]; then
        print_warning "Virtual environment already exists at $VENV_DIR"
        read -p "Recreate? (y/N) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$VENV_DIR"
            python3 -m venv "$VENV_DIR"
        fi
    else
        python3 -m venv "$VENV_DIR"
    fi

    print_success "Virtual environment created at $VENV_DIR"
}

# Install Python dependencies
install_python_deps() {
    print_status "Installing Python dependencies..."

    cd "$PROJECT_DIR"
    source "$VENV_DIR/bin/activate"

    # Upgrade pip
    pip install --upgrade pip wheel setuptools

    # Install base requirements
    pip install -r requirements.txt

    print_success "Python dependencies installed"
}

# Optional: Install MiniCPM-V
install_minicpm() {
    print_status "MiniCPM-V Installation"
    echo "MiniCPM-V is a local vision language model that can run on Raspberry Pi 5."
    echo "It requires significant disk space (~4GB) and memory."
    read -p "Install MiniCPM-V? (y/N) " -n 1 -r
    echo

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        source "$VENV_DIR/bin/activate"

        print_status "Installing PyTorch for ARM64..."
        pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

        print_status "Installing transformers..."
        pip install transformers accelerate

        print_status "Downloading MiniCPM-V model..."
        MODEL_DIR="$PROJECT_DIR/models/MiniCPM-V-2_6"
        mkdir -p "$MODEL_DIR"

        print_warning "Please download the model manually from HuggingFace:"
        echo "  https://huggingface.co/openbmb/MiniCPM-V-2_6"
        echo "  Save to: $MODEL_DIR"

        # Enable in config
        sed -i 's/enabled: false/enabled: true/' "$PROJECT_DIR/config/settings.yaml"

        print_success "MiniCPM-V setup complete"
    else
        print_status "Skipping MiniCPM-V installation"
    fi
}

# Optional: Install PaddleOCR
install_paddleocr() {
    print_status "PaddleOCR Installation"
    echo "PaddleOCR provides better OCR capabilities for the fallback strategy."
    read -p "Install PaddleOCR? (y/N) " -n 1 -r
    echo

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        source "$VENV_DIR/bin/activate"

        print_status "Installing PaddlePaddle (CPU version for ARM64)..."
        pip install paddlepaddle -i https://pypi.tuna.tsinghua.edu.cn/simple

        print_status "Installing PaddleOCR..."
        pip install paddleocr

        print_success "PaddleOCR installed"
    else
        print_status "Skipping PaddleOCR installation"
    fi
}

# Configure environment
configure_environment() {
    print_status "Configuring environment..."

    # Create necessary directories
    mkdir -p "$PROJECT_DIR/logs"
    mkdir -p "$PROJECT_DIR/models"

    # Set permissions
    chmod +x "$PROJECT_DIR/scripts/setup.sh"
    chmod +x "$PROJECT_DIR/scripts/test_connection.py"

    # Create activation script
    cat > "$PROJECT_DIR/activate" << 'EOF'
#!/bin/bash
# PixelClaw activation script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/venv/bin/activate"
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
export PIXELCLAW_HOME="$SCRIPT_DIR"

echo "PixelClaw environment activated"
echo "Python: $(which python)"
echo "Project: $SCRIPT_DIR"
EOF

    chmod +x "$PROJECT_DIR/activate"

    print_success "Environment configured"
}

# Print final instructions
print_instructions() {
    echo -e "\n${GREEN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                    Setup Complete!                           ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}\n"

    echo "Next steps:"
    echo ""
    echo "1. Activate the environment:"
    echo "   source ./activate"
    echo ""
    echo "2. Configure your device in config/devices.json:"
    echo "   - Set your Pixel 8a IP address"
    echo "   - Set the pairing code (from Developer Options)"
    echo ""
    echo "3. Configure API keys in config/api_keys.json:"
    echo "   - Add your Step-1V API key"
    echo ""
    echo "4. Test the connection:"
    echo "   python scripts/test_connection.py --full"
    echo ""
    echo "5. Start using PixelClaw:"
    echo "   python -m pixelclaw"
    echo ""
    echo "For more information, see README.md"
    echo ""
}

# Main setup flow
main() {
    print_status "Starting PixelClaw setup..."

    check_platform
    install_system_deps
    install_android_sdk
    setup_python_env
    install_python_deps
    install_minicpm
    install_paddleocr
    configure_environment

    print_instructions
}

# Run main function
main "$@"
