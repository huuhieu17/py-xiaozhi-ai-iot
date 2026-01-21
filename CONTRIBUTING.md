# Đóng Góp cho Xiaozhi AI-IoT

Cảm ơn bạn đã quan tâm đến việc đóng góp cho dự án! 🎉

## 🚀 Cách Đóng Góp

### 1. Fork & Clone

```bash
# Fork repo trên GitHub, sau đó:
git clone https://github.com/YOUR_USERNAME/py-xiaozhi-ai-iot.git
cd py-xiaozhi-ai-iot
```

### 2. Tạo Branch

```bash
git checkout -b feature/ten-tinh-nang
# hoặc
git checkout -b fix/ten-bug
```

### 3. Setup Development

```bash
# Tạo virtual environment
python3 -m venv venv
source venv/bin/activate

# Cài dependencies
pip install -r requirements.txt

# Copy config
cp config/config.example.json config/config.json
```

### 4. Code & Test

```bash
# Chạy app để test
python main.py --mode gui --skip-activation

# Chạy quick diagnostic
python scripts/quick_test.py
```

### 5. Commit

Sử dụng [Conventional Commits](https://www.conventionalcommits.org/):

```bash
git add .
git commit -m "feat: mô tả tính năng mới"
# hoặc
git commit -m "fix: mô tả bug đã sửa"
```

**Commit types:**
- `feat`: Tính năng mới
- `fix`: Sửa lỗi
- `docs`: Cập nhật documentation
- `style`: Format code (không thay đổi logic)
- `refactor`: Refactor code
- `test`: Thêm tests
- `chore`: Công việc khác

### 6. Push & Pull Request

```bash
git push origin feature/ten-tinh-nang
```

Sau đó tạo Pull Request trên GitHub.

---

## 📋 Quy Tắc Code

### Python Style

- Sử dụng **PEP 8** style guide
- Type hints cho function parameters và return values
- Docstrings cho classes và public functions
- Tên biến/function: `snake_case`
- Tên class: `PascalCase`

```python
def calculate_score(user_input: str, threshold: float = 0.5) -> float:
    """Tính điểm dựa trên input của user.
    
    Args:
        user_input: Văn bản đầu vào
        threshold: Ngưỡng tối thiểu
        
    Returns:
        Điểm số từ 0.0 đến 1.0
    """
    pass
```

### Exception Handling

```python
# ❌ Không làm
try:
    do_something()
except:
    pass

# ✅ Làm đúng
try:
    do_something()
except SpecificError as e:
    logger.error(f"Error occurred: {e}")
```

### Logging

```python
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

logger.info("Thông tin")
logger.warning("Cảnh báo")
logger.error("Lỗi", exc_info=True)
```

---

## 🏗️ Kiến Trúc

### Plugin System

Để thêm plugin mới:

```python
# src/plugins/my_plugin.py
from src.plugins.base import Plugin

class MyPlugin(Plugin):
    name = "my_plugin"
    
    async def setup(self, app):
        self.app = app
        
    async def start(self):
        # Khởi động plugin
        pass
        
    async def stop(self):
        # Dừng plugin
        pass
```

### MCP Tools

Để thêm MCP tool mới:

```python
# src/mcp/tools/my_tool/
├── __init__.py
└── my_tool.py
```

---

## 🧪 Testing

```bash
# Chạy diagnostic
python scripts/quick_test.py

# Test audio
python scripts/check_audio_wifi.py
```

---

## 📝 Checklist trước khi PR

- [ ] Code tuân thủ PEP 8
- [ ] Có type hints
- [ ] Có docstrings
- [ ] Không có bare `except:`
- [ ] Đã test trên local
- [ ] Commit message đúng format
- [ ] Không commit file nhạy cảm (config.json, tokens)

---

## 🙋 Câu Hỏi?

Nếu bạn có câu hỏi, hãy:
1. Mở Issue trên GitHub
2. Tham gia Facebook Group
3. Liên hệ qua website

Cảm ơn bạn đã đóng góp! 🙏
