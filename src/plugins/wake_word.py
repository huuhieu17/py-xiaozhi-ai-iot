import asyncio
import math
from array import array
from typing import Any

from src.constants.constants import AbortReason
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class WakeWordPlugin(Plugin):
    name = "wake_word"

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self.detector = None
        self._wake_beep_sound = None

    async def setup(self, app: Any) -> None:
        self.app = app
        logger.info("WakeWordPlugin: bắt đầu khởi tạo...")
        try:
            from src.audio_processing.wake_word_detect import WakeWordDetector

            self.detector = WakeWordDetector()
            if not getattr(self.detector, "enabled", False):
                logger.warning(
                    "WakeWordPlugin: detector bị tắt hoặc khởi tạo Porcupine thất bại."
                )
                self.detector = None
                return

            # Gắn kết callback
            self.detector.on_detected(self._on_detected)
            self.detector.on_error = self._on_error
            logger.info("WakeWordPlugin: khởi tạo thành công.")
        except ImportError as error:
            self.detector = None
            logger.error("WakeWordPlugin: thiếu dependency: %s", error)
        except Exception as error:
            self.detector = None
            logger.error("WakeWordPlugin: khởi tạo thất bại: %s", error, exc_info=True)

    async def start(self) -> None:
        if not self.detector:
            logger.warning("WakeWordPlugin: detector chưa sẵn sàng, bỏ qua start.")
            return
        try:
            # Cần bộ giải mã âm thanh để cung cấp dữ liệu PCM thô
            audio_codec = getattr(self.app, "audio_codec", None)
            if audio_codec is None:
                logger.warning("WakeWordPlugin: audio_codec not found in app, detection will not start.")
                return
            started = await self.detector.start(audio_codec)
            if started:
                logger.info("WakeWordPlugin: đã bắt đầu lắng nghe wake word.")
            else:
                logger.warning("WakeWordPlugin: không thể bắt đầu detector.")
        except Exception as e:
            # Log but don't crash if wake word fails to start
            logger.error("WakeWordPlugin: bắt đầu detection thất bại: %s", e, exc_info=True)

    async def stop(self) -> None:
        if self.detector:
            try:
                await self.detector.stop()
            except Exception:
                pass

    async def shutdown(self) -> None:
        if self.detector:
            try:
                await self.detector.stop()
            except Exception:
                pass

    async def _on_detected(self, wake_word, full_text):
        # Phát hiện từ đánh thức: chuyển sang đối thoại tự động (tự động chọn thời gian thực/dừng tự động dựa trên AEC)
        try:
            logger.info(f"🎤 WakeWordPlugin: Detected '{wake_word}' - '{full_text}'")
            # Nếu đang nói, để logic ngắt/máy trạng thái của ứng dụng xử lý
            if hasattr(self.app, "device_state") and hasattr(
                self.app, "start_auto_conversation"
            ):
                if self.app.is_speaking():
                    logger.info("Interrupting current speech...")
                    await self.app.abort_speaking(AbortReason.WAKE_WORD_DETECTED)
                    audio_plugin = self.app.plugins.get_plugin("audio")
                    if audio_plugin:
                        await audio_plugin.codec.clear_audio_queue()
                else:
                    logger.info("Starting auto conversation...")
                    await self.app.start_auto_conversation()
            else:
                logger.warning("App doesn't have required methods for auto conversation")
        except Exception as e:
            logger.error(f"Error handling wake word detection: {e}", exc_info=True)


    def _on_error(self, error):
        try:
            if hasattr(self.app, "set_chat_message"):
                self.app.set_chat_message("assistant", f"[Lỗi KWS] {error}")
        except Exception:
            pass
