# Changelog

All notable changes to Smart C AI will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `LICENSE` - MIT License for open source compliance
- `CHANGELOG.md` - Track version changes
- `scripts/common.sh` - Common shell functions to reduce code duplication

### Fixed
- Fixed bare `except:` clause in `scripts/quick_test.py` - now catches specific exceptions
- Fixed bare `except:` clause in `src/display/gui_display.py` - replaced with `except Exception:`
- Fixed potential shell injection in `scripts/quick_test.py` - replaced `subprocess.run(..., shell=True)` with safer `os.path.exists()`

### Improved
- Enhanced error handling with descriptive error messages
- Added docstrings to shell command execution functions

## [1.0.0] - 2026-01-21

### Added
- Initial release of Smart C AI for Raspberry Pi
- Voice interaction with AI
- Wake word detection (Alexa, Hey Lily, Smart C)
- WiFi provisioning with Hotspot mode
- Full HD GUI (1920x1080)
- Device activation with server
- Auto-update on boot

### Features
- PyQt5/QML-based modern UI
- Wayland/labwc support
- PulseAudio integration
- NetworkManager-based WiFi management
- Systemd service support

---

*For more details, see the [README.md](README.md)*
