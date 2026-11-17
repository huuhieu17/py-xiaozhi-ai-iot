import asyncio
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import sherpa_onnx

from src.constants.constants import AudioConfig
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger
from src.utils.resource_finder import resource_finder

logger = get_logger(__name__)


class WakeWordDetector:
    def __init__(self) -> None:
        self.audio_codec = None
        self.is_running_flag = False
        self.paused = False
        self.detection_task: Optional[asyncio.Task] = None
        self.last_detection_time = 0.0
        self.detection_cooldown = 1.5
        self.on_detected_callback: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
        self.keyword_spotter = None
        self.stream = None
        self.enabled = False

        config = ConfigManager.get_instance()
        use_wake_word = config.get_config("WAKE_WORD_OPTIONS.USE_WAKE_WORD", False)
        logger.info("WakeWordDetector: USE_WAKE_WORD=%s", use_wake_word)
        if not use_wake_word:
            logger.info("Chức năng từ đánh thức đã bị vô hiệu hóa")
            return

        self.enabled = True
        self.sample_rate = AudioConfig.INPUT_SAMPLE_RATE
        self._load_config(config)
        self._init_kws_model()
        self._validate_config()

    def _load_config(self, config) -> None:
        model_path = config.get_config("WAKE_WORD_OPTIONS.MODEL_PATH", "models")
        self.model_dir = resource_finder.find_directory(model_path) or Path(model_path)
        self.num_threads = config.get_config("WAKE_WORD_OPTIONS.NUM_THREADS", 4)
        self.provider = config.get_config("WAKE_WORD_OPTIONS.PROVIDER", "cpu")
        self.max_active_paths = config.get_config(
            "WAKE_WORD_OPTIONS.MAX_ACTIVE_PATHS", 2
        )
        self.keywords_score = config.get_config(
            "WAKE_WORD_OPTIONS.KEYWORDS_SCORE", 1.8
        )
        self.keywords_threshold = config.get_config(
            "WAKE_WORD_OPTIONS.KEYWORDS_THRESHOLD", 0.2
        )
        self.num_trailing_blanks = config.get_config(
            "WAKE_WORD_OPTIONS.NUM_TRAILING_BLANKS", 1
        )
        logger.info(
            "KWS config: model=%s, threshold=%s, score=%s",
            self.model_dir,
            self.keywords_threshold,
            self.keywords_score,
        )

    def _init_kws_model(self) -> None:
        try:
            paths = {
                name: self.model_dir / name
                for name in (
                    "encoder.onnx",
                    "decoder.onnx",
                    "joiner.onnx",
                    "tokens.txt",
                    "keywords.txt",
                )
            }
            missing = [str(path) for path in paths.values() if not path.is_file()]
            if missing:
                raise FileNotFoundError(f"Thiếu file model: {', '.join(missing)}")

            logger.info("Đang tải Sherpa-ONNX KeywordSpotter: %s", self.model_dir)
            self.keyword_spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(paths["tokens.txt"]),
                encoder=str(paths["encoder.onnx"]),
                decoder=str(paths["decoder.onnx"]),
                joiner=str(paths["joiner.onnx"]),
                keywords_file=str(paths["keywords.txt"]),
                num_threads=self.num_threads,
                sample_rate=self.sample_rate,
                feature_dim=80,
                max_active_paths=self.max_active_paths,
                keywords_score=self.keywords_score,
                keywords_threshold=self.keywords_threshold,
                num_trailing_blanks=self.num_trailing_blanks,
                provider=self.provider,
            )
            logger.info("Tải Sherpa-ONNX KeywordSpotter thành công")
        except Exception as error:
            self.enabled = False
            logger.error("Khởi tạo KeywordSpotter thất bại: %s", error, exc_info=True)

    def _validate_config(self) -> None:
        if not self.enabled:
            return
        if not 0.1 <= self.keywords_threshold <= 1.0:
            self.keywords_threshold = 0.25
        if not 0.1 <= self.keywords_score <= 10.0:
            self.keywords_score = 2.0

    def on_detected(self, callback: Callable) -> None:
        self.on_detected_callback = callback

    async def start(self, audio_codec) -> bool:
        if not self.enabled or not self.keyword_spotter:
            logger.warning("KeywordSpotter chưa sẵn sàng")
            return False
        try:
            self.audio_codec = audio_codec
            self.stream = self.keyword_spotter.create_stream()
            self.is_running_flag = True
            self.paused = False
            self.detection_task = asyncio.create_task(self._detection_loop())
            logger.info("Bộ phát hiện Sherpa-ONNX đã khởi động")
            return True
        except Exception as error:
            logger.error("Khởi động KeywordSpotter thất bại: %s", error, exc_info=True)
            return False

    async def _detection_loop(self) -> None:
        while self.is_running_flag:
            try:
                if self.paused or not self.audio_codec:
                    await asyncio.sleep(0.1)
                    continue
                await self._process_audio()
                await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                break
            except Exception as error:
                logger.error("Lỗi vòng lặp KWS: %s", error, exc_info=True)
                await asyncio.sleep(1)

    async def _process_audio(self) -> None:
        if not self.stream or not self.audio_codec:
            return
        for _ in range(3):
            data = await self.audio_codec.get_raw_audio_for_detection()
            if not data:
                continue
            if isinstance(data, bytes):
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            else:
                samples = np.asarray(data, dtype=np.float32)
            self.stream.accept_waveform(
                sample_rate=self.sample_rate, waveform=samples
            )

        while self.keyword_spotter.is_ready(self.stream):
            self.keyword_spotter.decode_stream(self.stream)
            result = self.keyword_spotter.get_result(self.stream)
            if result:
                logger.info("Wake word detected: %s", result)
                await self._handle_detection_result(result)
                self.keyword_spotter.reset_stream(self.stream)
                break

    async def _handle_detection_result(self, result) -> None:
        now = time.monotonic()
        if now - self.last_detection_time < self.detection_cooldown:
            return
        self.last_detection_time = now
        if not self.on_detected_callback:
            logger.warning("Chưa đăng ký callback wake word")
            return
        try:
            if asyncio.iscoroutinefunction(self.on_detected_callback):
                await self.on_detected_callback(result, result)
            else:
                self.on_detected_callback(result, result)
        except Exception as error:
            logger.error("Callback wake word thất bại: %s", error, exc_info=True)

    async def stop(self) -> None:
        self.is_running_flag = False
        if self.detection_task:
            self.detection_task.cancel()
            try:
                await self.detection_task
            except asyncio.CancelledError:
                pass
        self.detection_task = None
        self.stream = None
        logger.info("Bộ phát hiện Sherpa-ONNX đã dừng")

    async def pause(self) -> None:
        self.paused = True

    async def resume(self) -> None:
        self.paused = False

    def is_running(self) -> bool:
        return self.is_running_flag and not self.paused
