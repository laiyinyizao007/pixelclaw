# PixelClaw

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Platform: Linux/ARM64](https://img.shields.io/badge/platform-Linux%20ARM64-green.svg)](https://www.raspberrypi.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

**OpenClaw Automation System for Android Devices**

PixelClaw is a vision-based automation system for controlling Android devices using multi-level fallback strategies. Designed specifically for Raspberry Pi 5 (aarch64) to control devices like the Pixel 8a.

```
┌─────────────────────────────────────────────────────────────────┐
│                    VISION AGENT                                  │
├─────────────────────────────────────────────────────────────────┤
│  Goal → Analyze Screen → Choose Action → Execute → Verify       │
└─────────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │   Step-1V    │  │  MiniCPM-V   │  │  OCR + Rules │
    │  (Cloud VLM) │  │ (Local VLM)  │  │   (Local)    │
    └──────────────┘  └──────────────┘  └──────────────┘
           │                  │                  │
           └──────────────────┴──────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │   ADB Control    │
                    │  (Device Action) │
                    └──────────────────┘
```

## Features

- **🎯 Multi-Level Fallback Strategy**: Automatically falls back from cloud VLM to local models to OCR
- **🔄 Auto-Reconnection**: Monitors connection health and automatically reconnects
- **📱 Pixel 8a Optimized**: Pre-configured for Pixel 8a with wireless debugging
- **🧠 Vision-Based Control**: Uses screenshots and vision-language models for understanding UI
- **🔧 Extensible Architecture**: Modular design for easy extension

## Quick Start

### 1. Install Dependencies

```bash
# Clone the repository
git clone https://github.com/laiyinyizao007/pixelclaw.git
cd pixelclaw

# Run the setup script
bash scripts/setup.sh
```

### 2. Configure Your Device

Edit `config/devices.json` with your Pixel 8a details:

```json
{
  "devices": [{
    "id": "pixel8a_001",
    "name": "Pixel 8a",
    "ip": "172.19.0.1",
    "port": 45373,
    "pairing_code": "444047"
  }]
}
```

### 3. Configure API Keys

Edit `config/api_keys.json` with your Step-1V API key:

```json
{
  "step1v": {
    "api_key": "your-api-key-here",
    "base_url": "https://api.stepfun.com/v1"
  }
}
```

### 4. Connect and Test

> **Full setup guide (Chinese)**: [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)

```bash
# Install dependencies
pip install -r requirements.txt

# Connect device (Pixel 8a wireless debugging)
adb pair <ip:pairing-port> <pairing-code>
adb connect <ip:port>
adb devices -l  # Verify: should show "device"

# Run tests (no device or GPU required)
python -m pytest tests/ -v --import-mode=importlib
# Expected: 162 passed
```

> **Raspberry Pi users**: `bash scripts/setup.sh` installs ADB and generates an `activate` script (`source ./activate`).

### 5. Execute Automation Scenarios

```bash
# Boss直聘: Phase 1 — search and greet
python scenarios/boss/tasks/boss_greet_task.py --keyword "Python 工程师"

# Boss直聘: Phase 2 — apply after HR replies (run hours/day later)
python scenarios/boss/tasks/boss_apply_task.py --max-apply 20

# XHS scenario
python scenarios/xhs/tasks/xhs_recommend_task_v2.py

# Vision agent (generic task, requires Step-1V API key)
python -m pixelclaw --task "Open Settings app"
```

See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for end-to-end walkthrough.

## Architecture

### Fallback Strategy Hierarchy

```
Level 1: Step-1V (Cloud VLM by StepFun)
    ↓ [API failure / timeout / rate limit]
Level 2: MiniCPM-V (Local lightweight VLM)
    ↓ [Model unavailable / memory insufficient]
Level 3: OCR + Rule Matching (OpenCV + PaddleOCR)
    ↓ [Complete failure]
Level 4: Human Intervention
```

### Key Components

| Component | Description |
|-----------|-------------|
| `core/` | Device connector, Vision Agent, Screen analyzer, Action executor |
| `strategies/` | Fallback strategies: Step-1V, MiniCPM-V, OCR |
| `monitors/` | Connection monitoring, ADB manager, Shizuku manager |
| `services/` | Background keepalive service |
| `scripts/` | Setup and utility scripts |

## Project Structure

```
pixelclaw/
├── config/
│   ├── devices.json          # Device configuration
│   ├── api_keys.json         # API keys (gitignored)
│   ├── settings.yaml         # Global settings
│   ├── app_knowledge/        # Per-app UI element knowledge bases (JSON)
│   └── prompts/              # Agent prompt templates
├── core/
│   ├── device_connector.py   # Device connection management
│   ├── vision_agent.py       # Main automation agent
│   ├── som_annotator.py      # SoM screenshot annotation
│   ├── app_knowledge.py      # AppAgent knowledge loader
│   └── action_executor.py    # Action execution
├── strategies/
│   ├── base.py               # Strategy base classes
│   ├── fallback_manager.py   # Fallback orchestration
│   ├── step1v_strategy.py    # Step-1V cloud VLM
│   ├── minicpm_strategy.py   # MiniCPM-V local VLM
│   ├── ocr_strategy.py       # OCR + rule matching
│   ├── som_strategy.py       # SoM wrapper strategy
│   └── reflection_strategy.py# Post-action verification
├── monitors/
│   ├── connection_monitor.py # Health monitoring
│   ├── adb_manager.py        # ADB operations
│   └── shizuku_manager.py    # Shizuku integration
├── skills/
│   ├── boss/                 # Boss直聘 UIAutomator-based skill
│   └── xhs/                  # XHS (Little Red Book) skill
├── scenarios/
│   ├── boss/tasks/           # Boss job search & apply scripts
│   └── xhs/tasks/            # XHS automation scripts
├── tests/                    # Unit tests (162 tests, no device/GPU needed)
├── services/
│   └── keepalive_service.py  # Background service
├── scripts/
│   ├── setup.sh              # Raspberry Pi installation script
│   ├── test_connection.py    # Connection testing
│   └── install_minicpm.py    # MiniCPM-V installer
└── logs/                     # Log files
```

## Configuration

### Device Configuration

`config/devices.json`:

```json
{
  "devices": [{
    "id": "pixel8a_001",
    "name": "Pixel 8a",
    "type": "android",
    "model": "Pixel 8a",
    "ip": "172.19.0.1",
    "port": 45373,
    "pairing_code": "444047",
    "screen": {
      "width": 1080,
      "height": 2400,
      "dpi": 430
    }
  }],
  "active_device": "pixel8a_001"
}
```

### Strategy Configuration

`config/settings.yaml`:

```yaml
strategies:
  priority:
    - "step1v"
    - "minicpm"
    - "ocr"

  step1v:
    enabled: true
    api_key: ""  # From api_keys.json
    timeout: 30
    max_retries: 3

  minicpm:
    enabled: false  # Set true after installation
    model_path: "./models/MiniCPM-V-2_6"

  ocr:
    enabled: true
    engine: "paddleocr"
```

## Usage Examples

### Basic Connection

```python
from pixelclaw import DeviceConnector

connector = DeviceConnector()
connector.connect()
connector.test_connection()
```

### Execute Task

```python
import asyncio
from pixelclaw import DeviceConnector, VisionAgent, FallbackManager

async def main():
    connector = DeviceConnector()
    connector.connect()

    fallback = FallbackManager()
    agent = VisionAgent(connector, fallback)

    result = await agent.execute_task("Open Chrome and search for 'weather'")
    print(f"Success: {result.success}")
    print(f"Steps: {result.steps_taken}")

asyncio.run(main())
```

### Monitor Connection

```bash
# Start monitoring with live status panel
python -m pixelclaw --monitor --panel

# Run background service
python -m pixelclaw --service

# Check service status
python -m pixelclaw --service --status

# Stop service
python -m pixelclaw --service --stop
```

## Optional Components

### MiniCPM-V (Local VLM)

```bash
# Install MiniCPM-V for local vision inference
python scripts/install_minicpm.py

# Enable in config
# Edit config/settings.yaml: strategies.minicpm.enabled = true
```

### PaddleOCR

```bash
# Install PaddleOCR for better OCR fallback
pip install paddlepaddle paddleocr
```

## Device Setup (Pixel 8a)

1. **Enable Developer Options**:
   - Go to Settings → About Phone
   - Tap "Build Number" 7 times

2. **Enable Wireless Debugging**:
   - Settings → System → Developer Options
   - Enable "Wireless Debugging"
   - Tap "Pair code with QR code" or "Pair with pairing code"

3. **Note the IP and Port**:
   - IP and port will be displayed (e.g., `172.19.0.1:45373`)
   - Note the pairing code (e.g., `444047`)

4. **Update Configuration**:
   - Add IP, port, and pairing code to `config/devices.json`

## API Keys

### Step-1V (StepFun)

1. Sign up at [StepFun Platform](https://platform.stepfun.com)
2. Create an API key
3. Add to `config/api_keys.json`

## Testing

```bash
# Run all tests
pytest tests/

# Run connection test
python scripts/test_connection.py --full

# Test specific components
python scripts/test_connection.py --pair
python scripts/test_connection.py --connect
python scripts/test_connection.py --monitor
```

## Troubleshooting

### Connection Issues

```bash
# Check ADB installation
adb version

# List connected devices
adb devices -l

# Manual connection
adb connect 172.19.0.1:45373

# Pair device
adb pair 172.19.0.1:45373 444047
```

### Common Problems

| Problem | Solution |
|---------|----------|
| "Device unauthorized" | Accept the RSA key prompt on the device |
| "Connection refused" | Ensure wireless debugging is enabled |
| "Pairing failed" | Check IP, port, and pairing code |
| "Screenshot failed" | Grant storage permission to shell |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run formatter
black pixelclaw/ scripts/ tests/

# Run linter
ruff pixelclaw/ scripts/ tests/

# Run type checker
mypy pixelclaw/
```

## License

MIT License - See [LICENSE](LICENSE) for details.

## Acknowledgments

- [StepFun](https://platform.stepfun.com/) for Step-1V API
- [OpenBMB](https://github.com/OpenBMB) for MiniCPM-V
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) for OCR capabilities
