#!/usr/bin/env python3
"""
Script để chạy Local WebSocket Server độc lập
Dùng khi muốn server chạy trên máy khác hoặc riêng biệt
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

# Thêm thư mục gốc vào sys.path
try:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
except Exception:
    pass

from src.server.local_websocket_server import LocalWebSocketServer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description='Local WebSocket Server')
    parser.add_argument('--host', default='0.0.0.0', help='Server host (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=8765, help='Server port (default: 8765)')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    logger.info(f"🚀 Khởi động Local WebSocket Server")
    logger.info(f"📍 Host: {args.host}")
    logger.info(f"🔌 Port: {args.port}")
    
    server = LocalWebSocketServer(host=args.host, port=args.port)
    
    # Thiết lập callbacks
    async def on_client_connected(addr):
        logger.info(f"✅ Client kết nối: {addr}")

    async def on_client_disconnected(addr):
        logger.info(f"❌ Client ngắt kết nối: {addr}")

    async def on_message_received(addr, msg_type, data):
        logger.debug(f"📨 Nhận {msg_type} từ {addr}")

    server.on_client_connected = on_client_connected
    server.on_client_disconnected = on_client_disconnected
    server.on_message_received = on_message_received
    
    # Khởi động server
    try:
        await server.start()
        logger.info("✨ Server đang chờ kết nối...")
        logger.info("📋 Để dừng server, nhấn Ctrl+C")
        
        # Giữ server chạy
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("\n🛑 Dừng server...")
        await server.stop()
        logger.info("✔️ Server đã dừng")
    except Exception as e:
        logger.error(f"❌ Lỗi server: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
