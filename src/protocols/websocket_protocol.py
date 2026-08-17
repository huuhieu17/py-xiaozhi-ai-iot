import asyncio
import json
import ssl
import time

import websockets

from src.constants.constants import AudioConfig
from src.protocols.protocol import Protocol
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

ssl_context = ssl._create_unverified_context()

logger = get_logger(__name__)


class WebsocketProtocol(Protocol):
    def __init__(self):
        super().__init__()
        # Lấy phiên bản trình quản lý cấu hình
        self.config = ConfigManager.get_instance()
        self.websocket = None
        self.connected = False
        self.hello_received = None  # Khởi tạo là None
        # Tham chiếu tác vụ xử lý tin nhắn, tiện cho việc hủy khi đóng
        self._message_task = None

        # Giám sát tình trạng kết nối
        self._last_ping_time = None
        self._last_pong_time = None
        self._ping_interval = 30.0  # Khoảng thời gian nhịp tim (giây)
        self._ping_timeout = 10.0  # Thời gian chờ ping (giây)
        self._heartbeat_task = None
        self._connection_monitor_task = None

        # Cờ trạng thái kết nối
        self._is_closing = False
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 0  # Mặc định không kết nối lại
        self._auto_reconnect_enabled = False  # Mặc định tắt tự động kết nối lại

        self.WEBSOCKET_URL = self.config.get_config(
            "SYSTEM_OPTIONS.NETWORK.WEBSOCKET_URL"
        )
        access_token = self.config.get_config(
            "SYSTEM_OPTIONS.NETWORK.WEBSOCKET_ACCESS_TOKEN"
        )
        device_id = self.config.get_config("SYSTEM_OPTIONS.DEVICE_ID")
        client_id = self.config.get_config("SYSTEM_OPTIONS.CLIENT_ID")

        # Xây dựng headers, bỏ qua Authorization nếu token là None
        self.HEADERS = {
            "Protocol-Version": "1",
        }
        
        if access_token:
            self.HEADERS["Authorization"] = f"Bearer {access_token}"
        
        if device_id:
            self.HEADERS["Device-Id"] = device_id
            
        if client_id:
            self.HEADERS["Client-Id"] = client_id

    async def connect(self) -> bool:
        """
        Kết nối đến máy chủ WebSocket.
        """
        if self._is_closing:
            logger.warning("Kết nối đang đóng, hủy bỏ nỗ lực kết nối mới")
            return False

        try:
            # Tạo Event khi kết nối, đảm bảo trong đúng vòng lặp sự kiện
            self.hello_received = asyncio.Event()

            # Xác định xem có nên sử dụng SSL không
            current_ssl_context = None
            if self.WEBSOCKET_URL.startswith("wss://"):
                current_ssl_context = ssl_context

            # Thiết lập kết nối WebSocket (tương thích với các phiên bản Python khác nhau)
            try:
                # Cách viết mới (trong phiên bản Python 3.11+)
                self.websocket = await websockets.connect(
                    uri=self.WEBSOCKET_URL,
                    ssl=current_ssl_context,
                    additional_headers=self.HEADERS,
                    ping_interval=20,  # Sử dụng nhịp tim của websockets, khoảng thời gian 20 giây
                    ping_timeout=20,  # Thời gian chờ ping 20 giây
                    close_timeout=10,  # Thời gian chờ đóng 10 giây
                    max_size=10 * 1024 * 1024,  # Tin nhắn tối đa 10MB
                    compression=None,  # Tắt nén để cải thiện độ ổn định
                )
            except TypeError:
                # Cách viết cũ (trong phiên bản Python trước đó)
                self.websocket = await websockets.connect(
                    self.WEBSOCKET_URL,
                    ssl=current_ssl_context,
                    extra_headers=self.HEADERS,
                    ping_interval=20,  # Sử dụng nhịp tim của websockets
                    ping_timeout=20,  # Thời gian chờ ping 20 giây
                    close_timeout=10,  # Thời gian chờ đóng 10 giây
                    max_size=10 * 1024 * 1024,  # Tin nhắn tối đa 10MB
                    compression=None,  # Tắt nén
                )

            # Khởi động vòng lặp xử lý tin nhắn (lưu tham chiếu tác vụ, có thể hủy khi đóng)
            self._message_task = asyncio.create_task(self._message_handler())

            # Chú thích nhịp tim tùy chỉnh, sử dụng cơ chế nhịp tim tích hợp của websockets
            # self._start_heartbeat()

            # Khởi động giám sát kết nối
            self._start_connection_monitor()

            # Gửi tin nhắn hello từ client
            hello_message = {
                "type": "hello",
                "version": 1,
                "features": {
                    "mcp": True,
                },
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": AudioConfig.INPUT_SAMPLE_RATE,
                    "channels": AudioConfig.CHANNELS,
                    "frame_duration": AudioConfig.FRAME_DURATION,
                },
            }
            await self.send_text(json.dumps(hello_message))

            # Chờ phản hồi hello từ máy chủ
            try:
                await asyncio.wait_for(self.hello_received.wait(), timeout=10.0)
                self.connected = True
                self._reconnect_attempts = 0  # Đặt lại số lần thử kết nối lại
                logger.info("Đã kết nối đến máy chủ WebSocket")

                # Thông báo thay đổi trạng thái kết nối
                if self._on_connection_state_changed:
                    self._on_connection_state_changed(True, "Kết nối thành công")

                return True
            except asyncio.TimeoutError:
                logger.error("Chờ phản hồi hello từ máy chủ quá thời gian")
                await self._cleanup_connection()
                if self._on_network_error:
                    self._on_network_error("Chờ phản hồi quá thời gian")
                return False

        except Exception as e:
            logger.error(f"Kết nối WebSocket thất bại: {e}")
            await self._cleanup_connection()
            if self._on_network_error:
                self._on_network_error(f"Không thể kết nối dịch vụ: {str(e)}")
            return False

    def _start_heartbeat(self):
        """
        Khởi động tác vụ phát hiện nhịp tim.
        """
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def _start_connection_monitor(self):
        """
        Khởi động tác vụ giám sát kết nối.
        """
        if (
            self._connection_monitor_task is None
            or self._connection_monitor_task.done()
        ):
            self._connection_monitor_task = asyncio.create_task(
                self._connection_monitor()
            )

    async def _heartbeat_loop(self):
        """
        Vòng lặp phát hiện nhịp tim.
        """
        try:
            while self.websocket and not self._is_closing:
                await asyncio.sleep(self._ping_interval)

                if self.websocket and not self._is_closing:
                    try:
                        self._last_ping_time = time.time()
                        # Gửi ping và chờ phản hồi pong
                        pong_waiter = await self.websocket.ping()
                        logger.debug("Gửi ping nhịp tim")

                        # Chờ phản hồi pong
                        try:
                            await asyncio.wait_for(
                                pong_waiter, timeout=self._ping_timeout
                            )
                            self._last_pong_time = time.time()
                            logger.debug("Nhận phản hồi pong nhịp tim")
                        except asyncio.TimeoutError:
                            logger.warning("Phản hồi pong nhịp tim quá thời gian")
                            await self._handle_connection_loss("Pong nhịp tim quá thời gian")
                            break

                    except Exception as e:
                        logger.error(f"Gửi nhịp tim thất bại: {e}")
                        await self._handle_connection_loss("Gửi nhịp tim thất bại")
                        break
        except asyncio.CancelledError:
            logger.debug("Tác vụ nhịp tim bị hủy")
        except Exception as e:
            logger.error(f"Vòng lặp nhịp tim ngoại lệ: {e}")

    async def _connection_monitor(self):
        """
        Giám sát tình trạng sức khỏe kết nối.
        """
        try:
            while self.websocket and not self._is_closing:
                await asyncio.sleep(5)  # Kiểm tra mỗi 5 giây

                # Kiểm tra trạng thái kết nối
                if self.websocket:
                    if self.websocket.close_code is not None:
                        logger.warning("Phát hiện kết nối WebSocket đã đóng")
                        await self._handle_connection_loss("Kết nối đã đóng")
                        break

        except asyncio.CancelledError:
            logger.debug("Tác vụ giám sát kết nối bị hủy")
        except Exception as e:
            logger.error(f"Giám sát kết nối ngoại lệ: {e}")

    async def _handle_connection_loss(self, reason: str):
        """
        Xử lý mất kết nối.
        """
        logger.warning(f"Mất kết nối: {reason}")

        # Cập nhật trạng thái kết nối
        was_connected = self.connected
        self.connected = False

        # Thông báo thay đổi trạng thái kết nối
        if self._on_connection_state_changed and was_connected:
            try:
                self._on_connection_state_changed(False, reason)
            except Exception as e:
                logger.error(f"Gọi callback thay đổi trạng thái kết nối thất bại: {e}")

        # Dọn dẹp kết nối
        await self._cleanup_connection()

        # Thông báo đóng kênh âm thanh
        if self._on_audio_channel_closed:
            try:
                await self._on_audio_channel_closed()
            except Exception as e:
                logger.error(f"Gọi callback đóng kênh âm thanh thất bại: {e}")

        # Chỉ thử kết nối lại khi không phải đang đóng thủ công và bật tự động kết nối lại
        if (
            not self._is_closing
            and self._auto_reconnect_enabled
            and self._reconnect_attempts < self._max_reconnect_attempts
        ):
            await self._attempt_reconnect(reason)
        else:
            # Thông báo lỗi mạng
            if self._on_network_error:
                if (
                    self._auto_reconnect_enabled
                    and self._reconnect_attempts >= self._max_reconnect_attempts
                ):
                    self._on_network_error(f"Mất kết nối và kết nối lại thất bại: {reason}")
                else:
                    self._on_network_error(f"Mất kết nối: {reason}")

    async def _attempt_reconnect(self, original_reason: str):
        """
        Thử tự động kết nối lại.
        """
        self._reconnect_attempts += 1

        # Thông báo bắt đầu kết nối lại
        if self._on_reconnecting:
            try:
                self._on_reconnecting(
                    self._reconnect_attempts, self._max_reconnect_attempts
                )
            except Exception as e:
                logger.error(f"Gọi callback kết nối lại thất bại: {e}")

        logger.info(
            f"Thử tự động kết nối lại ({self._reconnect_attempts}/{self._max_reconnect_attempts})"
        )

        # Chờ một khoảng thời gian trước khi kết nối lại
        await asyncio.sleep(min(self._reconnect_attempts * 2, 30))  # Tăng theo cấp số nhân, tối đa 30 giây

        try:
            success = await self.connect()
            if success:
                logger.info("Tự động kết nối lại thành công")
                # Thông báo trạng thái kết nối thay đổi
                if self._on_connection_state_changed:
                    self._on_connection_state_changed(True, "Kết nối lại thành công")
            else:
                logger.warning(
                    f"Tự động kết nối lại thất bại ({self._reconnect_attempts}/{self._max_reconnect_attempts})"
                )
                # Nếu còn có thể thử lại, không báo lỗi ngay
                if self._reconnect_attempts >= self._max_reconnect_attempts:
                    if self._on_network_error:
                        self._on_network_error(
                            f"Kết nối lại thất bại, đã đạt số lần thử tối đa: {original_reason}"
                        )
        except Exception as e:
            logger.error(f"Lỗi trong quá trình kết nối lại: {e}")
            if self._reconnect_attempts >= self._max_reconnect_attempts:
                if self._on_network_error:
                    self._on_network_error(f"Ngoại lệ kết nối lại: {str(e)}")

    def enable_auto_reconnect(self, enabled: bool = True, max_attempts: int = 5):
        """Bật hoặc tắt tính năng tự động kết nối lại.

        Args:
            enabled: Có bật tự động kết nối lại hay không
            max_attempts: Số lần thử tối đa
        """
        self._auto_reconnect_enabled = enabled
        if enabled:
            self._max_reconnect_attempts = max_attempts
            logger.info(f"Đã bật tự động kết nối lại, số lần thử tối đa: {max_attempts}")
        else:
            self._max_reconnect_attempts = 0
            logger.info("Đã tắt tự động kết nối lại")

    def get_connection_info(self) -> dict:
        """Lấy thông tin kết nối.

        Returns:
            dict: Từ điển chứa trạng thái kết nối, số lần thử kết nối lại, v.v.
        """
        return {
            "connected": self.connected,
            "websocket_closed": (
                self.websocket.close_code is not None if self.websocket else True
            ),
            "is_closing": self._is_closing,
            "auto_reconnect_enabled": self._auto_reconnect_enabled,
            "reconnect_attempts": self._reconnect_attempts,
            "max_reconnect_attempts": self._max_reconnect_attempts,
            "last_ping_time": self._last_ping_time,
            "last_pong_time": self._last_pong_time,
            "websocket_url": self.WEBSOCKET_URL,
        }

    async def _message_handler(self):
        """
        Xử lý tin nhắn WebSocket nhận được.
        """
        try:
            async for message in self.websocket:
                if self._is_closing:
                    break

                try:
                    if isinstance(message, str):
                        try:
                            data = json.loads(message)
                            msg_type = data.get("type")
                            if msg_type == "hello":
                                # Xử lý tin nhắn hello từ máy chủ
                                await self._handle_server_hello(data)
                            else:
                                if self._on_incoming_json:
                                    self._on_incoming_json(data)
                        except json.JSONDecodeError as e:
                            logger.error(f"Tin nhắn JSON không hợp lệ: {message}, Lỗi: {e}")
                    elif isinstance(message, bytes):
                        # Tin nhắn nhị phân, có thể là âm thanh
                        if self._on_incoming_audio:
                            self._on_incoming_audio(message)
                except Exception as e:
                    # Xử lý lỗi tin nhắn đơn lẻ, nhưng tiếp tục xử lý các tin nhắn khác
                    logger.error(f"Lỗi xử lý tin nhắn: {e}", exc_info=True)
                    continue

        except asyncio.CancelledError:
            logger.debug("Tác vụ xử lý tin nhắn bị hủy")
            return
        except websockets.ConnectionClosed as e:
            if not self._is_closing:
                logger.info(f"Kết nối WebSocket đã đóng: {e}")
                await self._handle_connection_loss(f"Kết nối đóng: {e.code} {e.reason}")
        except websockets.ConnectionClosedError as e:
            if not self._is_closing:
                logger.info(f"Kết nối WebSocket đóng do lỗi: {e}")
                await self._handle_connection_loss(f"Lỗi kết nối: {e.code} {e.reason}")
        except websockets.InvalidState as e:
            logger.error(f"Trạng thái WebSocket không hợp lệ: {e}")
            await self._handle_connection_loss("Trạng thái kết nối bất thường")
        except ConnectionResetError:
            logger.warning("Kết nối bị đặt lại")
            await self._handle_connection_loss("Kết nối bị đặt lại")
        except OSError as e:
            logger.error(f"Lỗi I/O mạng: {e}")
            await self._handle_connection_loss("Lỗi I/O mạng")
        except Exception as e:
            logger.error(f"Vòng lặp xử lý tin nhắn ngoại lệ: {e}", exc_info=True)
            await self._handle_connection_loss(f"Xử lý tin nhắn ngoại lệ: {str(e)}")

    async def send_audio(self, data: bytes):
        """
        Gửi dữ liệu âm thanh.
        """
        if not self.is_audio_channel_opened():
            return

        try:
            await self.websocket.send(data)
        except websockets.ConnectionClosed as e:
            logger.warning(f"Kết nối đóng khi gửi âm thanh: {e}")
            await self._handle_connection_loss(f"Gửi âm thanh thất bại: {e.code} {e.reason}")
        except websockets.ConnectionClosedError as e:
            logger.warning(f"Lỗi kết nối khi gửi âm thanh: {e}")
            await self._handle_connection_loss(f"Lỗi gửi âm thanh: {e.code} {e.reason}")
        except Exception as e:
            logger.error(f"Gửi dữ liệu âm thanh thất bại: {e}")
            # Không gọi callback lỗi mạng ở đây, để bộ xử lý kết nối giải quyết
            await self._handle_connection_loss(f"Gửi âm thanh ngoại lệ: {str(e)}")

    async def send_text(self, message: str):
        """
        Gửi tin nhắn văn bản.
        """
        if not self.websocket or self._is_closing:
            logger.warning("WebSocket chưa kết nối hoặc đang đóng, không thể gửi tin nhắn")
            return

        try:
            await self.websocket.send(message)
        except websockets.ConnectionClosed as e:
            logger.warning(f"Kết nối đóng khi gửi văn bản: {e}")
            await self._handle_connection_loss(f"Gửi văn bản thất bại: {e.code} {e.reason}")
        except websockets.ConnectionClosedError as e:
            logger.warning(f"Lỗi kết nối khi gửi văn bản: {e}")
            await self._handle_connection_loss(f"Lỗi gửi văn bản: {e.code} {e.reason}")
        except Exception as e:
            logger.error(f"Gửi tin nhắn văn bản thất bại: {e}")
            await self._handle_connection_loss(f"Gửi văn bản ngoại lệ: {str(e)}")

    def is_audio_channel_opened(self) -> bool:
        """Kiểm tra xem kênh âm thanh đã mở hay chưa.

        Kiểm tra chính xác hơn trạng thái kết nối, bao gồm trạng thái thực tế của WebSocket
        """
        if not self.websocket or not self.connected or self._is_closing:
            return False

        # Kiểm tra trạng thái thực tế của WebSocket
        try:
            return self.websocket.close_code is None
        except Exception:
            return False

    async def open_audio_channel(self) -> bool:
        """Thiết lập kết nối WebSocket.

        Nếu chưa kết nối, tạo kết nối WebSocket mới
        Returns:
            bool: Kết nối thành công hay không
        """
        if not self.is_audio_channel_opened():
            return await self.connect()
        return True

    async def _handle_server_hello(self, data: dict):
        """
        Xử lý tin nhắn hello từ máy chủ.
        """
        try:
            # Xác minh phương thức truyền
            transport = data.get("transport")
            if not transport or transport != "websocket":
                logger.error(f"Phương thức truyền không được hỗ trợ: {transport}")
                return

            # Đặt sự kiện nhận hello
            self.hello_received.set()

            # Thông báo kênh âm thanh đã mở
            if self._on_audio_channel_opened:
                await self._on_audio_channel_opened()

            logger.info("Xử lý tin nhắn hello từ máy chủ thành công")

        except Exception as e:
            logger.error(f"Lỗi khi xử lý tin nhắn hello từ máy chủ: {e}")
            if self._on_network_error:
                self._on_network_error(f"Xử lý phản hồi từ máy chủ thất bại: {str(e)}")

    async def _cleanup_connection(self):
        """
        Dọn dẹp tài nguyên liên quan đến kết nối.
        """
        self.connected = False

        # Hủy tác vụ xử lý tin nhắn, ngăn chặn việc chờ đợi treo sau khi vòng lặp sự kiện thoát
        if self._message_task and not self._message_task.done():
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Ngoại lệ khi chờ hủy tác vụ tin nhắn: {e}")
        self._message_task = None

        # Hủy tác vụ nhịp tim
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Hủy tác vụ giám sát kết nối
        if self._connection_monitor_task and not self._connection_monitor_task.done():
            self._connection_monitor_task.cancel()
            try:
                await self._connection_monitor_task
            except asyncio.CancelledError:
                pass

        # Đóng kết nối WebSocket
        if self.websocket and self.websocket.close_code is None:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.error(f"Lỗi khi đóng kết nối WebSocket: {e}")

        self.websocket = None
        self._last_ping_time = None
        self._last_pong_time = None

    async def close_audio_channel(self):
        """
        Đóng kênh âm thanh.
        """
        self._is_closing = True

        try:
            await self._cleanup_connection()

            if self._on_audio_channel_closed:
                await self._on_audio_channel_closed()

        except Exception as e:
            logger.error(f"Đóng kênh âm thanh thất bại: {e}")
        finally:
            self._is_closing = False
