import asyncio
import math
from array import array
from typing import Any

from src.constants.constants import AbortReason
from src.plugins.base import Plugin


class WakeWordPlugin(Plugin):
    name = "wake_word"

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self.detector = None
        self._wake_beep_sound = None

    async def setup(self, app: Any) -> None:
        self.app = app
        try:
            from src.audio_processing.wake_word_detect import WakeWordDetector

            self.detector = WakeWordDetector()
            if not getattr(self.detector, "enabled", False):
                self.detector = None
                return

            # Gắn kết callback
            self.detector.on_detected(self._on_detected)
            self.detector.on_error = self._on_error
        except ImportError:
            # WakeWordDetector module not available
            self.detector = None
        except Exception:
            # Other initialization errors
            self.detector = None

    async def start(self) -> None:
        if not self.detector:
            return
        try:
            # Cần bộ giải mã âm thanh để cung cấp dữ liệu PCM thô
            audio_codec = getattr(self.app, "audio_codec", None)
            if audio_codec is None:
                from src.utils.logging_config import get_logger
                logger = get_logger(__name__)
                logger.warning("WakeWordPlugin: audio_codec not found in app, detection will not start.")
                return
            await self.detector.start(audio_codec)
        except Exception as e:
            # Log but don't crash if wake word fails to start
            from src.utils.logging_config import get_logger
            get_logger(__name__).warning(f"Failed to start wake word detection: {e}")

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
        from src.utils.logging_config import get_logger
        logger = get_logger(__name__)
        
        try:
            logger.info(f"🎤 WakeWordPlugin: Detected '{wake_word}' - '{full_text}'")
            await self._play_wake_beep()
            
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

    async def _play_wake_beep(self) -> None:
        try:
            await asyncio.to_thread(self._play_wake_beep_sync)
        except Exception:
            pass

    def _play_wake_beep_sync(self) -> None:
        from src.utils.logging_config import get_logger
        logger = get_logger(__name__)

        try:
            import pygame
        except Exception:
            return

        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=16000, size=-16, channels=1, buffer=512)

            if self._wake_beep_sound is None:
                sample_rate = 16000
                duration_sec = 0.12
                frequency_hz = 1100.0
                amplitude = 0.18

                sample_count = max(1, int(sample_rate * duration_sec))
                samples = array("h")
                for i in range(sample_count):
                    t = i / sample_rate
                    fade = min(1.0, i / 200.0, (sample_count - i) / 200.0)
                    value = int(32767 * amplitude * fade * math.sin(2.0 * math.pi * frequency_hz * t))
                    samples.append(value)

                self._wake_beep_sound = pygame.mixer.Sound(buffer=samples.tobytes())

            channel = pygame.mixer.find_channel(True)
            if channel:
                channel.play(self._wake_beep_sound)
            else:
                self._wake_beep_sound.play()
        except Exception as e:
            logger.debug(f"Wake beep playback skipped: {e}")

    def _on_error(self, error):
        try:
            if hasattr(self.app, "set_chat_message"):
                self.app.set_chat_message("assistant", f"[Lỗi KWS] {error}")
        except Exception:
            pass
