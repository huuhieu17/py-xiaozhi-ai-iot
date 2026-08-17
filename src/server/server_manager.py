"""
Server manager - khởi động và quản lý local websocket server
"""
import asyncio
import logging
from typing import Optional

from src.server.local_websocket_server import LocalWebSocketServer
from src.utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class LocalWebSocketServerManager:
    """Quản lý local websocket server"""

    _instance: Optional["LocalWebSocketServerManager"] = None
    _server: Optional[LocalWebSocketServer] = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls) -> "LocalWebSocketServerManager":
        """Lấy instance (singleton)"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = LocalWebSocketServerManager()
        return cls._instance

    async def initialize(self) -> bool:
        """Khởi tạo server"""
        try:
            config = ConfigManager.get_instance()
            
            # Kiểm tra xem có nên sử dụng local server không
            use_local = config.get_config(
                "SYSTEM_OPTIONS.NETWORK.USE_LOCAL_WEBSOCKET_SERVER", False
            )
            
            if not use_local:
                logger.info("Local WebSocket Server không được bật")
                return False

            host = config.get_config(
                "SYSTEM_OPTIONS.NETWORK.LOCAL_WEBSOCKET_HOST", "0.0.0.0"
            )
            port = config.get_config(
                "SYSTEM_OPTIONS.NETWORK.LOCAL_WEBSOCKET_PORT", 8765
            )

            self._server = LocalWebSocketServer(host=host, port=int(port))

            # Thiết lập callbacks
            self._server.on_client_connected = self._on_client_connected
            self._server.on_client_disconnected = self._on_client_disconnected
            self._server.on_message_received = self._on_message_received

            logger.info(f"LocalWebSocketServerManager khởi tạo thành công")
            return True

        except Exception as e:
            logger.error(f"Lỗi khởi tạo LocalWebSocketServerManager: {e}", exc_info=True)
            return False

    async def start(self) -> bool:
        """Khởi động server"""
        try:
            if self._server is None:
                if not await self.initialize():
                    return False

            if not self._server.is_running:
                await self._server.start()
                logger.info("Local WebSocket Server đã khởi động")
                return True

        except Exception as e:
            logger.error(f"Lỗi khởi động server: {e}", exc_info=True)
            return False

    async def stop(self) -> None:
        """Dừng server"""
        try:
            if self._server and self._server.is_running:
                await self._server.stop()
                logger.info("Local WebSocket Server đã dừng")
        except Exception as e:
            logger.error(f"Lỗi dừng server: {e}", exc_info=True)

    async def _on_client_connected(self, addr) -> None:
        """Callback khi client kết nối"""
        logger.info(f"🔗 Client kết nối: {addr}")

    async def _on_client_disconnected(self, addr) -> None:
        """Callback khi client ngắt kết nối"""
        logger.info(f"🔌 Client ngắt kết nối: {addr}")

    async def _on_message_received(self, addr, msg_type: str, data: dict) -> None:
        """Callback khi nhận tin nhắn"""
        logger.debug(f"📨 Nhận {msg_type} từ {addr}")

    def get_server_info(self) -> dict:
        """Lấy thông tin server"""
        if not self._server:
            return {"status": "not_initialized"}

        return {
            "status": "running" if self._server.is_running else "stopped",
            "host": self._server.host,
            "port": self._server.port,
            "connected_clients": self._server.get_connected_clients_count(),
            "sessions": len(self._server.get_all_sessions()),
        }
