import asyncio
import sys
import threading
from pathlib import Path
from typing import Any, Awaitable

# Cho phép chạy trực tiếp như một script: thêm thư mục gốc của dự án vào sys.path (trên cùng src)
try:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
except Exception:
    pass

from src.constants.constants import DeviceState, ListeningMode
from src.plugins.calendar import CalendarPlugin
from src.plugins.iot import IoTPlugin
from src.plugins.manager import PluginManager
from src.plugins.mcp import McpPlugin
from src.plugins.shortcuts import ShortcutsPlugin
from src.plugins.ui import UIPlugin
from src.plugins.web_control import WebControlPlugin
from src.plugins.wake_word import WakeWordPlugin
from src.protocols.mqtt_protocol import MqttProtocol
from src.protocols.websocket_protocol import WebsocketProtocol
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger
from src.utils.opus_loader import setup_opus

logger = get_logger(__name__)
setup_opus()


class Application:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = Application()
        return cls._instance

    def __init__(self):
        if Application._instance is not None:
            logger.error("Cố gắng tạo nhiều thể hiện của Application")
            raise Exception("Application là lớp singleton, vui lòng sử dụng get_instance() để lấy thể hiện")
        Application._instance = self

        logger.debug("Khởi tạo thể hiện Application")

        # Cấu hình
        self.config = ConfigManager.get_instance()

        # Trạng thái
        self.running = False
        self.protocol = None

        # Trạng thái thiết bị (chỉ chương trình chính có thể sửa đổi, plugin chỉ đọc)
        self.device_state = DeviceState.IDLE
        try:
            aec_enabled_cfg = bool(self.config.get_config("AEC_OPTIONS.ENABLED", True))
        except Exception:
            aec_enabled_cfg = True
        self.aec_enabled = aec_enabled_cfg
        # Force REALTIME mode for continuous listening (TV use case)
        # Tự động lắng nghe liên tục sau mỗi câu trả lời, bất kể có AEC hay không
        self.listening_mode = ListeningMode.REALTIME
        self.keep_listening = False
        self.aborted = False

        # Tập hợp nhiệm vụ đồng nhất (thay thế _main_tasks/_bg_tasks)
        self._tasks: set[asyncio.Task] = set()

        # Sự kiện dừng
        self._shutdown_event: asyncio.Event | None = None

        # Vòng lặp sự kiện
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # Kiểm soát đồng thời
        self._state_lock: asyncio.Lock | None = None
        self._connect_lock: asyncio.Lock | None = None

        # Plugin
        self.plugins = PluginManager()

    # -------------------------
    # Vòng đời
    # -------------------------
    async def run(self, *, protocol: str = "websocket", mode: str = "gui") -> int:
        logger.info("Khởi động Application, protocol=%s", protocol)
        try:
            self.running = True
            self._main_loop = asyncio.get_running_loop()
            self._initialize_async_objects()
            self._set_protocol(protocol)
            self._setup_protocol_callbacks()
            # Plugin: setup (hoãn nhập AudioPlugin, đảm bảo setup_opus đã thực thi)
            from src.plugins.audio import AudioPlugin

            # Đăng ký plugin âm thanh, UI, MCP, IoT, từ khóa đánh thức, phím tắt và lịch (chế độ UI từ tham số run)
            self.plugins.register(
                McpPlugin(),
                IoTPlugin(),
                AudioPlugin(),
                WakeWordPlugin(),
                CalendarPlugin(),
                UIPlugin(mode=mode),
                ShortcutsPlugin(),
                WebControlPlugin(),
            )
            await self.plugins.setup_all(self)
            # Sau khi khởi động, phát sóng trạng thái ban đầu, đảm bảo UI sẵn sàng thấy "Đang chờ"
            try:
                await self.plugins.notify_device_state_changed(self.device_state)
            except Exception:
                pass
            # Plugin: start (phải start trước khi connect để wake word sẵn sàng)
            await self.plugins.start_all()
            # Kết nối WebSocket trong background (không block startup)
            logger.info("Scheduling WebSocket connection in background...")
            self.spawn(self._auto_connect_protocol(), "auto-connect-protocol")
            # Chờ dừng
            await self._wait_shutdown()
            return 0

        except Exception as e:
            logger.error(f"Chạy ứng dụng thất bại: {e}", exc_info=True)
            return 1
        finally:
            try:
                await self.shutdown()
            except Exception as e:
                logger.error(f"Lỗi khi đóng ứng dụng: {e}")

    async def connect_protocol(self):
        """
        Đảm bảo kênh giao thức được mở và phát sóng một lần trạng thái kênh đã sẵn sàng. Trả về liệu có mở hay không.
        """
        # Nếu đã mở thì trả về ngay
        try:
            if self.is_audio_channel_opened():
                return True
            if not self._connect_lock:
                # Nếu chưa khởi tạo khóa, thử một lần
                opened = await asyncio.wait_for(
                    self.protocol.open_audio_channel(), timeout=12.0
                )
                if not opened:
                    logger.error("Kết nối giao thức thất bại")
                    return False
                logger.info("Kết nối giao thức đã được thiết lập, nhấn Ctrl+C để thoát")
                await self.plugins.notify_protocol_connected(self.protocol)
                return True

            async with self._connect_lock:
                if self.is_audio_channel_opened():
                    return True
                opened = await asyncio.wait_for(
                    self.protocol.open_audio_channel(), timeout=12.0
                )
                if not opened:
                    logger.error("Kết nối giao thức thất bại")
                    return False
                logger.info("Kết nối giao thức đã được thiết lập, nhấn Ctrl+C để thoát")
                await self.plugins.notify_protocol_connected(self.protocol)
                return True
        except asyncio.TimeoutError:
            logger.error("Kết nối giao thức bị timeout")
            return False

    def _initialize_async_objects(self) -> None:
        logger.debug("Khởi tạo đối tượng bất đồng bộ")
        self._shutdown_event = asyncio.Event()
        self._state_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()

    def _set_protocol(self, protocol_type: str) -> None:
        logger.debug("Thiết lập loại giao thức: %s", protocol_type)
        if protocol_type == "mqtt":
            self.protocol = MqttProtocol(asyncio.get_running_loop())
        else:
            self.protocol = WebsocketProtocol()

    # -------------------------
    # Nghe thủ công (giữ để nói)
    # -------------------------
    async def start_listening_manual(self) -> None:
        try:
            ok = await self.connect_protocol()
            if not ok:
                return
            self.keep_listening = False

            # Nếu đang nói thì gửi yêu cầu ngắt
            if self.device_state == DeviceState.SPEAKING:
                logger.info("Gửi yêu cầu ngắt khi đang nói")
                await self.protocol.send_abort_speaking(None)
                await self.set_device_state(DeviceState.IDLE)
            await self.protocol.send_start_listening(ListeningMode.MANUAL)
            await self.set_device_state(DeviceState.LISTENING)
        except Exception:
            pass

    async def stop_listening_manual(self) -> None:
        try:
            await self.protocol.send_stop_listening()
            await self.set_device_state(DeviceState.IDLE)
        except Exception:
            pass

    # -------------------------
    # Đối thoại tự động/thực thời: chọn chế độ dựa trên AEC và cấu hình hiện tại, mở giữ phiên
    # -------------------------
    async def start_auto_conversation(self) -> None:
        try:
            ok = await self.connect_protocol()
            if not ok:
                return

            # Force REALTIME mode for continuous listening
            mode = ListeningMode.REALTIME
            self.listening_mode = mode
            self.keep_listening = True
            await self.protocol.send_start_listening(mode)
            await self.set_device_state(DeviceState.LISTENING)
        except Exception:
            pass

    def _setup_protocol_callbacks(self) -> None:
        self.protocol.on_network_error(self._on_network_error)
        self.protocol.on_incoming_json(self._on_incoming_json)
        self.protocol.on_incoming_audio(self._on_incoming_audio)
        self.protocol.on_audio_channel_opened(self._on_audio_channel_opened)
        self.protocol.on_audio_channel_closed(self._on_audio_channel_closed)

    async def _auto_connect_protocol(self) -> None:
        """
        Tự động kết nối WebSocket trong background, retry nếu thất bại.
        """
        retry_delay = 3
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Attempting WebSocket connection (attempt {attempt}/{max_retries})...")
                ok = await self.connect_protocol()
                if ok:
                    logger.info("WebSocket connected successfully!")
                    return
                else:
                    logger.warning(f"WebSocket connection failed, retrying in {retry_delay}s...")
            except Exception as e:
                logger.error(f"WebSocket connection error: {e}", exc_info=True)
            
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
        
        logger.error(f"Failed to connect WebSocket after {max_retries} attempts. Wake word will work but responses require manual connection.")

    async def _wait_shutdown(self) -> None:
        await self._shutdown_event.wait()

    # -------------------------
    # Quản lý nhiệm vụ đồng nhất (tinh gọn)
    # -------------------------
    def spawn(self, coro: Awaitable[Any], name: str) -> asyncio.Task:
        """
        Tạo nhiệm vụ và đăng ký, hủy bỏ khi dừng.
        """
        if not self.running or (self._shutdown_event and self._shutdown_event.is_set()):
            logger.debug(f"Bỏ qua việc tạo nhiệm vụ (ứng dụng đang đóng): {name}")
            return None
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)

        def _done(t: asyncio.Task):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.error(f"Nhiệm vụ {name} kết thúc với ngoại lệ: {t.exception()}", exc_info=True)

        task.add_done_callback(_done)
        return task

    def schedule_command_nowait(self, fn, *args, **kwargs) -> None:
        """Lên lịch "ngay lập tức": đưa bất kỳ callable nào trở lại vòng lặp chính để thực thi.

        - Nếu trả về coroutine, nó sẽ được tự động tạo nhiệm vụ con để thực thi (fire-and-forget).
        - Nếu là hàm đồng bộ, nó sẽ chạy trực tiếp trong luồng vòng lặp sự kiện (cố gắng giữ nhẹ).
        """
        if not self._main_loop or self._main_loop.is_closed():
            logger.warning("Vòng lặp sự kiện chính chưa sẵn sàng, từ chối lên lịch")
            return

        def _runner():
            try:
                res = fn(*args, **kwargs)
                if asyncio.iscoroutine(res):
                    self.spawn(res, name=f"call:{getattr(fn, '__name__', 'anon')}")
            except Exception as e:
                logger.error(f"Thực thi callable được lên lịch thất bại: {e}", exc_info=True)

        # Đảm bảo thực thi trong luồng vòng lặp sự kiện
        self._main_loop.call_soon_threadsafe(_runner)

    # -------------------------
    # Callback giao thức
    # -------------------------
    def _on_network_error(self, error_message=None):
        if error_message:
            logger.error(error_message)

        self.keep_listening = False
        # Lỗi thì yêu cầu đóng
        # if self._shutdown_event and not self._shutdown_event.is_set():
        #     self._shutdown_event.set()

    def _on_incoming_audio(self, data: bytes):
        logger.info(f"📥 Nhận audio từ server, độ dài: {len(data)} bytes")
        # Chuyển tiếp cho plugin
        self.spawn(self.plugins.notify_incoming_audio(data), "plugin:on_audio")

    def _on_incoming_json(self, json_data):
        try:
            msg_type = json_data.get("type") if isinstance(json_data, dict) else None
            logger.info(f"Nhận tin nhắn JSON: type={msg_type}")
            # Chuyển đổi TTS start/stop thành trạng thái thiết bị (hỗ trợ tự động/thực thời, không làm ô nhiễm chế độ thủ công)
            if msg_type == "tts":
                state = json_data.get("state")
                if state == "start":
                    # Chỉ khi giữ phiên và ở chế độ thực thời, trong thời gian TTS bắt đầu thì giữ LISTENING; nếu không thì hiển thị SPEAKING
                    if (
                        self.keep_listening
                        and self.listening_mode == ListeningMode.REALTIME
                    ):
                        self.spawn(
                            self.set_device_state(DeviceState.LISTENING),
                            "state:tts_start_rt",
                        )
                    else:
                        self.spawn(
                            self.set_device_state(DeviceState.SPEAKING),
                            "state:tts_start_speaking",
                        )
                elif state == "stop":
                    if self.keep_listening:
                        # Tiếp tục đối thoại: khởi động lại nghe dựa trên chế độ hiện tại
                        async def _restart_listening():
                            try:
                                # REALTIME và đã ở LISTENING thì không cần gửi lại
                                if not (
                                    self.listening_mode == ListeningMode.REALTIME
                                    and self.device_state == DeviceState.LISTENING
                                ):
                                    await self.protocol.send_start_listening(
                                        self.listening_mode
                                    )
                            except Exception:
                                pass
                            self.keep_listening and await self.set_device_state(
                                DeviceState.LISTENING
                            )

                        self.spawn(_restart_listening(), "state:tts_stop_restart")
                    else:
                        self.spawn(
                            self.set_device_state(DeviceState.IDLE),
                            "state:tts_stop_idle",
                        )
            # Chuyển tiếp cho plugin
            self.spawn(self.plugins.notify_incoming_json(json_data), "plugin:on_json")
        except Exception:
            logger.info("Nhận tin nhắn JSON")

    async def _on_audio_channel_opened(self):
        logger.info("Kênh giao thức đã mở")
        # Sau khi kênh mở vào LISTENING (đơn giản hóa thành đọc và ghi trực tiếp)
        await self.set_device_state(DeviceState.LISTENING)

    async def _on_audio_channel_closed(self):
        logger.info("Kênh giao thức đã đóng")
        # Sau khi kênh đóng quay về IDLE
        await self.set_device_state(DeviceState.IDLE)

    async def set_device_state(self, state: DeviceState):
        """
        Chỉ dành cho gọi nội bộ của chương trình chính: thiết lập trạng thái thiết bị. Plugin chỉ có thể đọc.
        """
        # print(f"set_device_state: {state}")
        if not self._state_lock:
            self.device_state = state
            try:
                await self.plugins.notify_device_state_changed(state)
            except Exception:
                pass
            return
        async with self._state_lock:
            if self.device_state == state:
                return
            logger.info(f"Thiết lập trạng thái thiết bị: {state}")
            self.device_state = state
        # Phát sóng bên ngoài khóa, tránh callback plugin gây tắc nghẽn lâu dài
        try:
            await self.plugins.notify_device_state_changed(state)
            if state == DeviceState.LISTENING:
                await asyncio.sleep(0.5)
                self.aborted = False
        except Exception:
            pass

    # -------------------------
    # Truy cập chỉ đọc (cung cấp cho plugin sử dụng)
    # -------------------------
    def get_device_state(self):
        return self.device_state

    def is_idle(self) -> bool:
        return self.device_state == DeviceState.IDLE

    def is_listening(self) -> bool:
        return self.device_state == DeviceState.LISTENING

    def is_speaking(self) -> bool:
        return self.device_state == DeviceState.SPEAKING

    def get_listening_mode(self):
        return self.listening_mode

    def is_keep_listening(self) -> bool:
        return bool(self.keep_listening)

    def is_audio_channel_opened(self) -> bool:
        try:
            return bool(self.protocol and self.protocol.is_audio_channel_opened())
        except Exception:
            return False

    def get_state_snapshot(self) -> dict:
        return {
            "device_state": self.device_state,
            "listening_mode": self.listening_mode,
            "keep_listening": bool(self.keep_listening),
            "audio_opened": self.is_audio_channel_opened(),
        }

    async def abort_speaking(self, reason):
        """
        Ngừng phát âm thanh.
        """

        if self.aborted:
            logger.debug(f"Đã ngừng, bỏ qua yêu cầu ngừng lặp lại: {reason}")
            return

        logger.info(f"Ngừng phát âm thanh, lý do: {reason}")
        self.aborted = True
        await self.protocol.send_abort_speaking(reason)
        await self.set_device_state(DeviceState.IDLE)

    # -------------------------
    # Hỗ trợ UI: cho phép plugin hoặc công cụ gọi trực tiếp
    # -------------------------
    def set_chat_message(self, role, message: str) -> None:
        """Chuyển đổi cập nhật văn bản thành tin nhắn JSON mà UI có thể nhận biết (tái sử dụng on_incoming_json của UIPlugin).
        role: "assistant" | "user" ảnh hưởng đến ánh xạ loại tin nhắn.
        """
        try:
            msg_type = "tts" if str(role).lower() == "assistant" else "stt"
        except Exception:
            msg_type = "tts"
        payload = {"type": msg_type, "text": message}
        # Phát sóng bất đồng bộ qua bus sự kiện plugin
        self.spawn(self.plugins.notify_incoming_json(payload), "ui:text_update")

    def set_emotion(self, emotion: str) -> None:
        """
        Thiết lập cảm xúc: thông qua on_incoming_json của UIPlugin.
        """
        payload = {"type": "llm", "emotion": emotion}
        self.spawn(self.plugins.notify_incoming_json(payload), "ui:emotion_update")

    # -------------------------
    # Dừng
    # -------------------------
    async def shutdown(self):
        if not self.running:
            return
        logger.info("Đang đóng Application...")
        self.running = False

        if self._shutdown_event is not None:
            self._shutdown_event.set()

        try:
            # Hủy tất cả nhiệm vụ đã đăng ký
            if self._tasks:
                for t in list(self._tasks):
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)
                self._tasks.clear()

            # Đóng giao thức (có thời gian giới hạn, tránh chặn thoát)
            if self.protocol:
                try:
                    try:
                        self._main_loop.create_task(self.protocol.close_audio_channel())
                    except asyncio.TimeoutError:
                        logger.warning("Đóng giao thức bị timeout, bỏ qua chờ đợi")
                except Exception as e:
                    logger.error(f"Đóng giao thức thất bại: {e}")

            # Plugin: stop/shutdown
            try:
                await self.plugins.stop_all()
            except Exception:
                pass
            try:
                await self.plugins.shutdown_all()
            except Exception:
                pass

            logger.info("Đóng Application hoàn tất")
        except Exception as e:
            logger.error(f"Lỗi khi đóng ứng dụng: {e}", exc_info=True)
