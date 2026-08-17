# Local WebSocket Server Guide

## 🎯 Giới Thiệu

Dự án đã được cấu hình để sử dụng **Local WebSocket Server** thay vì server bên ngoài. Điều này giúp:

- ✅ Không cần authorization từ server bên ngoài
- ✅ Phát triển và thử nghiệm nhanh hơn
- ✅ Tránh lỗi "Protocol is not connected"
- ✅ Hoàn toàn cục bộ, không phụ thuộc internet

## 🚀 Cách Sử Dụng

### 1. **Tự động khởi động (Khuyến nghị)**

Local WebSocket Server sẽ tự động khởi động cùng với ứng dụng chính:

```bash
python main.py
```

Khi ứng dụng khởi động, bạn sẽ thấy:
```
✨ Local WebSocket Server sẽ khởi động trong background
🚀 WebSocket Server đang chạy tại ws://0.0.0.0:8765
```

### 2. **Chạy Server Độc Lập**

Nếu bạn muốn chạy server trên port khác hoặc máy khác:

```bash
# Chạy với cài đặt mặc định (0.0.0.0:8765)
python run_local_ws_server.py

# Chạy với port tuỳ chỉnh
python run_local_ws_server.py --port 9000

# Chạy với host tuỳ chỉnh
python run_local_ws_server.py --host 192.168.1.100 --port 8765

# Bật debug logging
python run_local_ws_server.py --debug
```

## 📝 Cấu Hình

### Config File: `config/config.json`

```json
{
  "SYSTEM_OPTIONS": {
    "NETWORK": {
      "WEBSOCKET_URL": "ws://localhost:8765",
      "WEBSOCKET_ACCESS_TOKEN": null,
      "AUTHORIZATION_URL": null,
      "USE_LOCAL_WEBSOCKET_SERVER": true,
      "LOCAL_WEBSOCKET_HOST": "0.0.0.0",
      "LOCAL_WEBSOCKET_PORT": 8765
    }
  }
}
```

**Giải thích:**
- `WEBSOCKET_URL`: Địa chỉ local websocket server
- `WEBSOCKET_ACCESS_TOKEN`: `null` (không cần token)
- `AUTHORIZATION_URL`: `null` (bỏ qua authorization)
- `USE_LOCAL_WEBSOCKET_SERVER`: `true` (bật local server)
- `LOCAL_WEBSOCKET_HOST`: Host cho local server
- `LOCAL_WEBSOCKET_PORT`: Port cho local server

### Để Sử Dụng Server Bên Ngoài (Tuỳ Chọn)

Nếu muốn quay lại server bên ngoài, cập nhật config:

```json
{
  "SYSTEM_OPTIONS": {
    "NETWORK": {
      "WEBSOCKET_URL": "wss://xiaozhi-ai-iot.vn/api/v1/ws",
      "WEBSOCKET_ACCESS_TOKEN": "your-token-here",
      "AUTHORIZATION_URL": "https://xiaozhi-ai-iot.vn/",
      "USE_LOCAL_WEBSOCKET_SERVER": false
    }
  }
}
```

## 📋 Tính Năng

Local WebSocket Server hỗ trợ:

### ✅ Hello Handshake
```json
Client gửi:
{
  "type": "hello",
  "version": 1,
  "features": {"mcp": true},
  "audio_params": {...}
}

Server phản hồi:
{
  "type": "hello",
  "version": 1,
  "session_id": "uuid-...",
  "server": "xiaozhi-local-websocket-server"
}
```

### ✅ Listen Commands
- `listen start`: Bắt đầu lắng nghe
- `listen stop`: Dừng lắng nghe
- `listen detect`: Phát hiện từ khoá

### ✅ Audio Data
- Nhận dữ liệu audio từ client
- Xử lý các command liên quan

### ✅ IoT Descriptors
- Nhận và xác nhận IoT device descriptors

### ✅ Abort Command
- Hỗ trợ ngắt lời nói

## 🔍 Debugging

### Xem Logs

Local WebSocket Server in ra thông tin chi tiết:

```
✅ Client kết nối: ('127.0.0.1', 54321)
✨ Session tạo mới: 12345678-1234-1234-1234-123456789012
🎤 Bắt đầu lắng nghe (session: 12345678..., mode: manual)
🛑 Dừng lắng nghe (session: 12345678...)
📤 Gửi phản hồi: Đây là phản hồi mô phỏng cho câu hỏi: '...'
❌ Client ngắt kết nối: ('127.0.0.1', 54321)
```

### Chế Độ Debug

Để xem logs chi tiết:

```bash
python run_local_ws_server.py --debug
```

Hoặc sửa `src/server/local_websocket_server.py`:

```python
logging.basicConfig(level=logging.DEBUG)
```

## 📦 Kiến Trúc

```
src/server/
├── __init__.py
├── local_websocket_server.py     # WebSocket server chính
└── server_manager.py              # Quản lý lifecycle

src/application.py                 # Tích hợp server vào app
src/plugins/web_control.py         # Sử dụng protocol
src/protocols/websocket_protocol.py # Kết nối đến local server

config/config.json                 # Cấu hình
run_local_ws_server.py            # Script chạy standalone
```

## 🛠️ Xử Lý Lỗi

### "Protocol is not connected"

**Nguyên Nhân:** WebSocket server không chạy hoặc client không kết nối được

**Giải Pháp:**
1. Đảm bảo `USE_LOCAL_WEBSOCKET_SERVER: true` trong config
2. Kiểm tra logs: `python run_local_ws_server.py --debug`
3. Kiểm tra firewall không chặn port 8765
4. Kiểm tra WEBSOCKET_URL đúng

### Connection Timeout

**Nguyên Nhân:** Server không phản hồi trong thời gian

**Giải Pháp:**
1. Kiểm tra server đang chạy: `netstat -an | grep 8765`
2. Khởi động lại server
3. Xem logs: `python run_local_ws_server.py --debug`

### "Address already in use"

**Nguyên Nhân:** Port 8765 đã được sử dụng

**Giải Pháp:**
```bash
# Linux/Mac: Tìm process
lsof -i :8765

# Windows: Tìm process
netstat -ano | findstr :8765

# Giết process hoặc chạy trên port khác
python run_local_ws_server.py --port 9000
```

## 📞 Support

Nếu gặp vấn đề, kiểm tra:

1. Logs của application
2. Logs của local websocket server
3. Config settings
4. Kết nối mạng
5. Firewall rules

---

**Happy developing! 🎉**
