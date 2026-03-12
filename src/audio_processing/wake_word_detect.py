import asyncio
import time
import pvporcupine
import numpy as np
from typing import Callable, Optional
from src.constants.constants import AudioConfig
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

class WakeWordDetector:
    def __init__(self):
        config = ConfigManager.get_instance()
        self.enabled = config.get_config("WAKE_WORD_OPTIONS.USE_WAKE_WORD", False)
        
        if not self.enabled:
            logger.info("Chức năng từ đánh thức đã bị vô hiệu hóa")
            return

        # Giữ nguyên các flag trạng thái
        self.is_running_flag = False
        self.paused = False
        self.audio_codec = None
        self.detection_task = None
        
        # Callback
        self.on_detected_callback: Optional[Callable] = None
        self.on_error: Optional[Callable] = None

        # Cấu hình Porcupine (Cực nhẹ cho RAM 512MB)
        self.access_key = config.get_config("WAKE_WORD_OPTIONS.PICOVOICE_API_KEY", "")
        self.keywords = config.get_config("WAKE_WORD_OPTIONS.KEYWORDS", ["porcupine"]) 
        self.sensitivities = [0.5] * len(self.keywords)

        self.porcupine = None
        self._init_porcupine()

    def _init_porcupine(self):
        try:
            self.porcupine = pvporcupine.create(
                access_key=self.access_key,
                keywords=self.keywords,
                sensitivities=self.sensitivities
            )
            logger.info(f"Đã khởi tạo Porcupine thành công với từ khóa: {self.keywords}")
        except Exception as e:
            logger.error(f"Khởi tạo Porcupine thất bại: {e}")
            self.enabled = False

    def on_detected(self, callback: Callable):
        self.on_detected_callback = callback

    async def start(self, audio_codec) -> bool:
        if not self.enabled or not self.porcupine:
            return False
        
        self.audio_codec = audio_codec
        self.is_running_flag = True
        self.paused = False
        self.detection_task = asyncio.create_task(self._detection_loop())
        logger.info("Bộ phát hiện Porcupine (siêu nhẹ) đã sẵn sàng")
        return True

    async def _detection_loop(self):
        # Porcupine yêu cầu độ dài khung hình cố định
        frame_length = self.porcupine.frame_length 
        
        while self.is_running_flag:
            try:
                if self.paused or not self.audio_codec:
                    await asyncio.sleep(0.1)
                    continue

                # Lấy dữ liệu thô từ audio_codec
                data = await self.audio_codec.get_raw_audio_for_detection()
                if not data:
                    await asyncio.sleep(0.01)
                    continue

                # Chuyển đổi sang định dạng Int16 mà Porcupine yêu cầu
                if isinstance(data, bytes):
                    pcm = np.frombuffer(data, dtype=np.int16)
                else:
                    # Nếu data là float, chuyển về int16
                    pcm = (np.array(data) * 32767).astype(np.int16)

                # Porcupine xử lý theo từng frame chuẩn
                # Nếu pcm dài hơn frame_length, ta cắt nhỏ ra
                for i in range(0, len(pcm) - frame_length + 1, frame_length):
                    frame = pcm[i : i + frame_length]
                    result_index = self.porcupine.process(frame)
                    
                    if result_index >= 0:
                        logger.info(f"🎯 Wake word detected via Porcupine!")
                        await self._handle_detection_result(self.keywords[result_index])

                await asyncio.sleep(0.001) # Nhịp nghỉ cực ngắn

            except Exception as e:
                logger.error(f"Lỗi vòng lặp Porcupine: {e}")
                await asyncio.sleep(1)

    async def _handle_detection_result(self, keyword):
        if self.on_detected_callback:
            try:
                # Giữ nguyên kiểu gọi callback để không hỏng code bên ngoài
                if asyncio.iscoroutinefunction(self.on_detected_callback):
                    await self.on_detected_callback(keyword, keyword)
                else:
                    self.on_detected_callback(keyword, keyword)
            except Exception as e:
                logger.error(f"Lỗi thực thi callback: {e}")

    async def stop(self):
        self.is_running_flag = False
        if self.detection_task:
            self.detection_task.cancel()
        if self.porcupine:
            self.porcupine.delete()
        logger.info("Đã dừng Porcupine")

    async def pause(self): self.paused = True
    async def resume(self): self.paused = False
    def is_running(self) -> bool: return self.is_running_flag and not self.paused
