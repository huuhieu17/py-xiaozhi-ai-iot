# 🤖 Xiaozhi AI-IoT - Trợ Lý AI Thông Minh cho Raspberry Pi

<p align="center">
  <img src="assets/icon.png" alt="Xiaozhi AI-IoT Logo" width="120" height="120">
</p>

<p align="center">
  <strong>Open-source AI Voice Assistant chạy trên Raspberry Pi với giao diện PyQt5/QML</strong>
</p>

<p align="center">
  <a href="https://github.com/Xiaozhi-IOT-AI/py-xiaozhi-ai-iot/stargazers">
    <img src="https://img.shields.io/github/stars/Xiaozhi-IOT-AI/py-xiaozhi-ai-iot?style=social" alt="Stars">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  </a>
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/Python-3.9+-blue.svg" alt="Python">
  </a>
  <a href="https://www.raspberrypi.org/">
    <img src="https://img.shields.io/badge/Raspberry%20Pi-4%2F5-red.svg" alt="Raspberry Pi">
  </a>
</p>

---

## 📖 Giới Thiệu

**Xiaozhi AI-IoT** là một trợ lý AI giọng nói hoàn chỉnh, được thiết kế để chạy trên Raspberry Pi. Dự án này cung cấp trải nghiệm voice interaction tương tự như Alexa, Google Assistant hay Siri, nhưng hoàn toàn **tự host** và **tùy biến được**.

### 🎯 Mục Tiêu Dự Án

- 🏠 **Smart Home Hub**: Điều khiển thiết bị IoT bằng giọng nói
- 🎤 **Always Listening**: Wake word detection với từ khóa tùy chỉnh
- 🖥️ **Beautiful UI**: Giao diện Full HD với hiệu ứng emotion động
- 📡 **Easy Setup**: WiFi provisioning qua Hotspot mode
- 🔄 **Auto Update**: Tự động cập nhật OTA

---

## ✨ Tính Năng

| Tính Năng | Mô Tả |
|-----------|-------|
| 🎤 **Voice Interaction** | Tương tác bằng giọng nói với AI |
| 🔊 **Wake Word Detection** | Luôn lắng nghe "Alexa", "Hey Lily", "Smart C", "Xiaozhi" |
| 📡 **WiFi Provisioning** | Tự động bật Hotspot khi chưa có WiFi |
| 🖥️ **Full HD GUI** | Giao diện 1920x1080, hỗ trợ Wayland/X11 |
| 🔐 **Device Activation** | Kích hoạt thiết bị với server |
| ⚡ **Auto-Update** | Tự động cập nhật khi khởi động |
| 🎭 **Emotion Display** | Hiển thị cảm xúc động (GIF/PNG) |
| 📷 **Camera Support** | Tích hợp camera/video stream |
| 🔌 **IoT Integration** | MCP tools cho smart home |

---

## 🏗️ Kiến Trúc Hệ Thống

```
┌─────────────────────────────────────────────────────────────────┐
│                         Xiaozhi AI-IoT                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │  Wake Word   │  │   Audio      │  │     GUI      │          │
│  │  Detection   │──│  Processing  │──│   Display    │          │
│  │ (sherpa-onnx)│  │  (WebRTC)    │  │   (PyQt5)    │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│         │                 │                 │                   │
│         └────────────┬────┴─────────────────┘                   │
│                      ▼                                          │
│  ┌─────────────────────────────────────────────────────┐       │
│  │              Application Core                        │       │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │       │
│  │  │ Plugins │ │Protocols│ │ Network │ │  Utils  │   │       │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘   │       │
│  └─────────────────────────────────────────────────────┘       │
│                      │                                          │
│         ┌────────────┴────────────┐                            │
│         ▼                         ▼                            │
│  ┌──────────────┐         ┌──────────────┐                     │
│  │  WebSocket   │         │    MQTT      │                     │
│  │  Protocol    │         │  Protocol    │                     │
│  └──────────────┘         └──────────────┘                     │
│         │                         │                            │
│         └───────────┬─────────────┘                            │
│                     ▼                                          │
│            ┌──────────────┐                                    │
│            │ AI Server    │                                    │
│            │ (Cloud/Local)│                                    │
│            └──────────────┘                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📁 Cấu Trúc Dự Án

```
xiaozhi-ai-iot/
├── main.py                     # Entry point
├── requirements.txt            # Python dependencies
├── pyproject.toml              # Project metadata
│
├── src/                        # Main source code
│   ├── application.py          # Core application (Singleton)
│   ├── audio_codecs/           # Opus encoder/decoder
│   ├── audio_processing/       # Wake word, VAD
│   ├── core/                   # System initializer, OTA
│   ├── display/                # GUI (PyQt5/QML)
│   ├── iot/                    # IoT device control
│   ├── mcp/                    # MCP tools (calendar, music, etc.)
│   ├── network/                # WiFi manager, Captive Portal
│   ├── plugins/                # Plugin system
│   ├── protocols/              # WebSocket, MQTT
│   └── utils/                  # Logging, config, helpers
│
├── config/                     # Configuration
│   └── config.example.json     # Template config
│
├── models/                     # ONNX wake word models
│   ├── encoder.onnx
│   ├── decoder.onnx
│   ├── joiner.onnx
│   └── keywords.txt
│
├── assets/                     # UI assets
│   ├── icon.png
│   ├── emojis/                 # Emotion GIFs
│   └── videos/                 # Background videos
│
├── scripts/                    # Utility scripts
│   ├── common.sh               # Shared functions
│   ├── fix_autostart.sh        # Fix autostart issues
│   ├── fix_display.sh          # Fix HDMI display
│   └── check_audio_wifi.py     # Diagnostic tool
│
├── libs/                       # Native libraries
│   ├── webrtc_apm/             # Echo cancellation (macOS)
│   └── libopus/                # Opus codec
│
├── install_oslite.sh           # Full installer (with GUI)
├── install_minimal.sh          # Minimal installer (CLI only)
├── run.sh                      # GUI launcher
├── update.sh                   # Manual update
└── auto_update.sh              # Auto-update on boot
```

---

## 🚀 Cài Đặt

### Yêu Cầu Phần Cứng

| Component | Yêu cầu |
|-----------|---------|
| **Board** | Raspberry Pi 4/5 (4GB+ RAM khuyến nghị) |
| **OS** | Raspberry Pi OS Lite (64-bit) |
| **Microphone** | USB Microphone |
| **Speaker** | 3.5mm Jack hoặc HDMI Audio |
| **Display** | HDMI Monitor (Full HD khuyến nghị) |
| **Network** | WiFi hoặc Ethernet |

### Cài Đặt Nhanh

```bash
# 1. Clone repository
git clone https://github.com/Xiaozhi-IOT-AI/py-xiaozhi-ai-iot.git ~/.digits
cd ~/.digits

# 2. Chạy installer
bash install_oslite.sh

# 3. Reboot
sudo reboot
```

### Các Bản Cài Đặt

| Bản | RAM | GUI | Autostart | Lệnh |
|-----|-----|-----|-----------|------|
| **Full** | ~400MB | PyQt5 Desktop | Desktop Entry | `bash install_oslite.sh` |
| **Minimal** | ~100MB | CLI only | systemd service | `bash install_minimal.sh` |

---

## �️ Phát Triển (Development)

### Yêu Cầu

- Python 3.9+
- PyQt5
- Git

### Setup Development Environment

```bash
# 1. Clone repo
git clone https://github.com/Xiaozhi-IOT-AI/py-xiaozhi-ai-iot.git
cd py-xiaozhi-ai-iot

# 2. Tạo virtual environment (khuyến nghị)
python3 -m venv venv
source venv/bin/activate

# 3. Cài đặt dependencies
pip install -r requirements.txt

# 4. Copy config
cp config/config.example.json config/config.json

# 5. Chạy ứng dụng
python main.py --mode gui
```

### Các Lệnh Chạy

```bash
# GUI mode (mặc định)
python main.py --mode gui

# CLI mode (không có giao diện)
python main.py --mode cli

# Bỏ qua activation (debug)
python main.py --mode gui --skip-activation

# Sử dụng MQTT protocol
python main.py --mode gui --protocol mqtt
```

### Project Structure cho Developer

```
Workflow khi tham gia dev:

1. Fork repo → Clone về máy local
2. Tạo branch mới: git checkout -b feature/ten-tinh-nang
3. Code và test
4. Commit: git commit -m "feat: mô tả"
5. Push và tạo Pull Request
```

---

## ⚙️ Cấu Hình

### File `config/config.json`

```json
{
  "SYSTEM_OPTIONS": {
    "NETWORK": {
      "WEBSOCKET_URL": "wss://your-server.com/api/v1/ws",
      "WEBSOCKET_ACCESS_TOKEN": null
    },
    "WINDOW_SIZE_MODE": "fullhd"
  },
  "WAKE_WORD_OPTIONS": {
    "USE_WAKE_WORD": true,
    "KEYWORDS_SCORE": 1.8
  },
  "AUDIO_DEVICES": {
    "input_device_name": "USB PnP Sound Device",
    "output_device_name": "bcm2835 Headphones"
  }
}
```

### Window Size Modes

| Mode | Kích thước | Mô tả |
|------|------------|-------|
| `fullhd` | 1920x1080 | Full HD (khuyến nghị) |
| `hd` | 1280x720 | HD |
| `screen_75` | 75% màn hình | Responsive |
| `screen_100` | Toàn màn hình | Fullscreen |

### Wake Words

| Từ khóa | Trigger | Độ nhạy |
|---------|---------|---------|
| `alexa` | @alexa | 1.8 |
| `hey lily` | @hey_lily | 1.8 |
| `smart c` | @smartc | 1.8 |
| `xiaozhi` | @xiaozhi | 1.8 |

---

## 📱 Luồng Hoạt Động

```
Boot Pi → Xiaozhi AI khởi động
              ↓
        Kiểm tra WiFi
        /           \
   Không có        Có WiFi
      ↓               ↓
 Bật Hotspot      First Run?
"SmartC-Setup"    /        \
      ↓         Có         Không
Captive Portal   ↓           ↓
192.168.4.1   Settings   Activated?
      ↓                  /        \
 Cấu hình WiFi       Chưa        Rồi
                       ↓           ↓
                   Activation → Chat Bot
                       ↓
              Nói "Alexa" hoặc "Hey Lily"
                       ↓
               AI responds 🎉
```

---

## 🔧 Troubleshooting

### Xem Logs

```bash
# Real-time logs
tail -f ~/.digits/logs/smartc.log

# Kiểm tra service status
sudo systemctl status smartc
```

### Các Lỗi Thường Gặp

| Lỗi | Nguyên nhân | Cách sửa |
|-----|-------------|----------|
| Không có âm thanh | Audio device sai | Chạy `bash scripts/check_audio_wifi.py` |
| GUI không hiện | Display config | `sudo bash scripts/fix_display.sh` |
| App không tự khởi động | Autostart lỗi | `bash scripts/fix_autostart.sh` |
| WiFi không kết nối | NetworkManager | Kiểm tra `nmcli` status |

### Quick Diagnostic

```bash
python scripts/quick_test.py
```

---

## 🤝 Đóng Góp

Chúng tôi hoan nghênh mọi đóng góp! Xem [CONTRIBUTING.md](CONTRIBUTING.md) để biết thêm chi tiết.

### Cách đóng góp

1. 🍴 Fork repo
2. 🌿 Tạo branch: `git checkout -b feature/amazing-feature`
3. � Commit: `git commit -m 'feat: Add amazing feature'`
4. 📤 Push: `git push origin feature/amazing-feature`
5. 🔀 Tạo Pull Request

### Commit Convention

```
feat: Thêm tính năng mới
fix: Sửa lỗi
docs: Cập nhật documentation
style: Format code
refactor: Refactor code
test: Thêm tests
chore: Công việc khác
```

---

## 📞 Liên Hệ & Cộng Đồng

- 🌐 **Website**: [xiaozhi-ai-iot.vn](https://xiaozhi-ai-iot.vn)
- 💬 **Facebook Group**: [Xiaozhi AI-IoT Vietnam](https://facebook.com/groups/xiaozhi-ai-iot)
- 📱 **Zalo Group**: Liên hệ qua website

---

## 📄 License

Dự án này được phát hành dưới giấy phép **MIT License**. Xem file [LICENSE](LICENSE) để biết thêm chi tiết.

---

## 🙏 Credits

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) - Wake word detection
- [PyQt5](https://www.riverbankcomputing.com/software/pyqt/) - GUI framework
- Cộng đồng Xiaozhi AI-IoT Vietnam

---

<p align="center">
  <b>Xiaozhi AI-IoT</b> - <i>Trợ lý AI thông minh cho mọi nhà</i> 🏠
</p>

<p align="center">
  Made with ❤️ by <a href="https://xiaozhi-ai-iot.vn">Xiaozhi AI-IoT Team</a>
</p>
