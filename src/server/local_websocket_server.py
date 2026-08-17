"""
Local WebSocket server - không cần authorization
Đây là server đơn giản để thay thế server bên ngoài cho mục đích phát triển/thử nghiệm
"""
import asyncio
import json
import logging
import uuid
from typing import Dict, Set, Callable

import websockets
from websockets.server import WebSocketServerProtocol

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LocalWebSocketServer:
    """
    Server WebSocket cục bộ với các tính năng cơ bản:
    - Nhận tin nhắn văn bản từ client
    - Nhận dữ liệu âm thanh từ client
    - Gửi phản hồi đơn giản
    - Không yêu cầu authorization
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.host = host
        self.port = port
        self.clients: Set[WebSocketServerProtocol] = set()
        self.sessions: Dict[str, dict] = {}  # Lưu trữ thông tin session
        self.server = None
        self.is_running = False

        # Callbacks (tuỳ chọn)
        self.on_client_connected: Callable | None = None
        self.on_client_disconnected: Callable | None = None
        self.on_message_received: Callable | None = None

    async def start(self) -> None:
        """Khởi động server"""
        try:
            self.server = await websockets.serve(
                self._handle_client,
                self.host,
                self.port,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=10,
                max_size=10 * 1024 * 1024,  # 10MB
            )
            self.is_running = True
            logger.info(f"🚀 WebSocket Server đang chạy tại ws://{self.host}:{self.port}")
        except Exception as e:
            logger.error(f"❌ Lỗi khởi động server: {e}")
            raise

    async def stop(self) -> None:
        """Dừng server"""
        self.is_running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("Server WebSocket đã dừng")

    async def _handle_client(self, websocket: WebSocketServerProtocol, path: str) -> None:
        """Xử lý kết nối client"""
        client_addr = websocket.remote_address
        logger.info(f"✅ Client kết nối: {client_addr}")

        # Thêm client vào danh sách
        self.clients.add(websocket)

        # Callback kết nối
        if self.on_client_connected:
            try:
                await self.on_client_connected(client_addr)
            except Exception as e:
                logger.error(f"Lỗi callback kết nối: {e}")

        try:
            async for message in websocket:
                try:
                    await self._handle_message(websocket, message)
                except Exception as e:
                    logger.error(f"Lỗi xử lý tin nhắn: {e}")

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"❌ Client ngắt kết nối: {client_addr}")
        except Exception as e:
            logger.error(f"Lỗi xử lý client: {e}")
        finally:
            # Xóa client khỏi danh sách
            self.clients.discard(websocket)

            # Callback ngắt kết nối
            if self.on_client_disconnected:
                try:
                    await self.on_client_disconnected(client_addr)
                except Exception as e:
                    logger.error(f"Lỗi callback ngắt kết nối: {e}")

    async def _handle_message(self, websocket: WebSocketServerProtocol, message: str | bytes) -> None:
        """Xử lý tin nhắn từ client"""

        # Xử lý tin nhắn văn bản
        if isinstance(message, str):
            try:
                data = json.loads(message)
                msg_type = data.get("type", "unknown")
                session_id = data.get("session_id", "")

                logger.debug(f"📨 Nhận tin nhắn từ {websocket.remote_address}: type={msg_type}, session={session_id}")

                # Xử lý hello handshake
                if msg_type == "hello":
                    await self._handle_hello(websocket, data)

                # Xử lý listen commands
                elif msg_type == "listen":
                    await self._handle_listen(websocket, data)

                # Xử lý abort command
                elif msg_type == "abort":
                    await self._handle_abort(websocket, data)

                # Xử lý IoT descriptors
                elif msg_type == "iot_descriptors":
                    await self._handle_iot_descriptors(websocket, data)

                # Xử lý các loại khác
                else:
                    logger.warning(f"⚠️ Loại tin nhắn không được hỗ trợ: {msg_type}")
                    await self._send_error(websocket, f"Unknown message type: {msg_type}")

                # Callback tin nhắn
                if self.on_message_received:
                    try:
                        await self.on_message_received(websocket.remote_address, msg_type, data)
                    except Exception as e:
                        logger.error(f"Lỗi callback tin nhắn: {e}")

            except json.JSONDecodeError as e:
                logger.error(f"❌ Lỗi JSON: {e}")
                await self._send_error(websocket, f"Invalid JSON: {str(e)}")

        # Xử lý dữ liệu audio (bytes)
        elif isinstance(message, bytes):
            logger.debug(f"🎵 Nhận audio từ {websocket.remote_address}: {len(message)} bytes")
            # Xử lý audio data nếu cần

    async def _handle_hello(self, websocket: WebSocketServerProtocol, data: dict) -> None:
        """Xử lý hello message từ client"""
        version = data.get("version", 1)
        features = data.get("features", {})

        # Tạo session_id nếu chưa có
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = {
            "client": websocket.remote_address,
            "version": version,
            "features": features,
            "state": "initialized",
        }

        logger.info(f"✨ Session tạo mới: {session_id}")

        # Gửi phản hồi hello
        response = {
            "type": "hello",
            "version": 1,
            "session_id": session_id,
            "server": "xiaozhi-local-websocket-server",
            "features": {
                "audio": True,
                "mcp": True,
                "listening_modes": ["manual", "realtime", "auto"],
            },
        }
        await websocket.send(json.dumps(response))
        logger.info(f"✅ Gửi hello response cho {websocket.remote_address}")

    async def _handle_listen(self, websocket: WebSocketServerProtocol, data: dict) -> None:
        """Xử lý listen commands"""
        session_id = data.get("session_id", "")
        state = data.get("state", "")
        mode = data.get("mode", "")
        text = data.get("text", "")

        if state == "start":
            logger.info(f"🎤 Bắt đầu lắng nghe (session: {session_id}, mode: {mode})")
            if session_id in self.sessions:
                self.sessions[session_id]["state"] = f"listening_{mode}"

            # Gửi phản hồi xác nhận
            response = {
                "type": "listen",
                "session_id": session_id,
                "state": "started",
                "mode": mode,
            }
            await websocket.send(json.dumps(response))

        elif state == "stop":
            logger.info(f"🛑 Dừng lắng nghe (session: {session_id})")
            if session_id in self.sessions:
                self.sessions[session_id]["state"] = "idle"

            response = {
                "type": "listen",
                "session_id": session_id,
                "state": "stopped",
            }
            await websocket.send(json.dumps(response))

        elif state == "detect":
            logger.info(f"🎯 Phát hiện: {text} (session: {session_id})")

            # Gửi phản hồi xác nhận
            response = {
                "type": "listen",
                "session_id": session_id,
                "state": "detected",
                "text": text,
            }
            await websocket.send(json.dumps(response))

            # Mô phỏng phản hồi từ AI
            await self._send_mock_response(websocket, session_id, text)

    async def _send_mock_response(self, websocket: WebSocketServerProtocol, session_id: str, query: str) -> None:
        """Gửi phản hồi mô phỏng từ AI"""
        # Chờ một chút rồi gửi phản hồi
        await asyncio.sleep(0.5)

        response_text = f"Đây là phản hồi mô phỏng cho câu hỏi: '{query}'"

        response = {
            "type": "response",
            "session_id": session_id,
            "text": response_text,
            "state": "text_response",
        }

        try:
            await websocket.send(json.dumps(response))
            logger.info(f"📤 Gửi phản hồi: {response_text}")
        except Exception as e:
            logger.error(f"Lỗi gửi phản hồi: {e}")

    async def _handle_abort(self, websocket: WebSocketServerProtocol, data: dict) -> None:
        """Xử lý abort command"""
        session_id = data.get("session_id", "")
        reason = data.get("reason", "")
        logger.info(f"⛔ Ngắt lời nói (session: {session_id}, reason: {reason})")

        if session_id in self.sessions:
            self.sessions[session_id]["state"] = "aborted"

        response = {"type": "abort_ack", "session_id": session_id}
        await websocket.send(json.dumps(response))

    async def _handle_iot_descriptors(self, websocket: WebSocketServerProtocol, data: dict) -> None:
        """Xử lý IoT descriptors"""
        session_id = data.get("session_id", "")
        logger.info(f"🏠 Nhận IoT descriptors (session: {session_id})")

        response = {"type": "iot_descriptors_ack", "session_id": session_id}
        await websocket.send(json.dumps(response))

    async def _send_error(self, websocket: WebSocketServerProtocol, error_msg: str) -> None:
        """Gửi tin nhắn lỗi"""
        error = {"type": "error", "message": error_msg}
        try:
            await websocket.send(json.dumps(error))
        except Exception as e:
            logger.error(f"Lỗi gửi error message: {e}")

    def get_session_info(self, session_id: str) -> dict | None:
        """Lấy thông tin session"""
        return self.sessions.get(session_id)

    def get_all_sessions(self) -> dict:
        """Lấy tất cả sessions"""
        return self.sessions.copy()

    def get_connected_clients_count(self) -> int:
        """Lấy số lượng client kết nối"""
        return len(self.clients)


async def main():
    """Chạy server"""
    server = LocalWebSocketServer(host="0.0.0.0", port=8765)

    # Thiết lập callbacks (tuỳ chọn)
    async def on_client_connected(addr):
        logger.info(f"🔗 Callback: Client kết nối từ {addr}")

    async def on_client_disconnected(addr):
        logger.info(f"🔌 Callback: Client ngắt kết nối từ {addr}")

    async def on_message_received(addr, msg_type, data):
        logger.info(f"📩 Callback: Nhận {msg_type} từ {addr}")

    server.on_client_connected = on_client_connected
    server.on_client_disconnected = on_client_disconnected
    server.on_message_received = on_message_received

    # Khởi động server
    await server.start()

    # Giữ server chạy
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Dừng server...")
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
