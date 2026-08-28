import asyncio
import gc
import time
from collections import deque
from typing import Optional

import numpy as np
import opuslib
import sounddevice as sd
import soxr

from src.audio_codecs.aec_processor import AECProcessor
from src.constants.constants import AudioConfig
from src.utils.config_manager import ConfigManager
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class AudioCodec:
    """
    Bộ giải mã âm thanh, chịu trách nhiệm mã hóa ghi âm và giải mã phát lại
    Chức năng chính:
    1. Ghi âm: Micro -> Lấy mẫu lại 16kHz -> Mã hóa Opus -> Gửi
    2. Phát lại: Nhận -> Giải mã Opus 24kHz -> Hàng đợi phát -> Loa
    """

    def __init__(self):
        # Lấy trình quản lý cấu hình
        self.config = ConfigManager.get_instance()

        # Bộ mã hóa Opus: Ghi âm 16kHz, Phát lại 24kHz
        self.opus_encoder = None
        self.opus_decoder = None

        # Thông tin thiết bị
        self.device_input_sample_rate = None
        self.device_output_sample_rate = None
        self.mic_device_id = None  # ID thiết bị micro (chỉ số cố định, không ghi đè sau khi ghi cấu hình)
        self.speaker_device_id = None  # ID thiết bị loa (chỉ số cố định)

        # Bộ lấy mẫu lại: Ghi âm lấy mẫu lại về 16kHz, phát lại lấy mẫu lại theo thiết bị
        self.input_resampler = None  # Tỷ lệ mẫu thiết bị -> 16kHz
        self.output_resampler = None  # 24kHz -> Tỷ lệ mẫu thiết bị (để phát)

        # Bộ đệm lấy mẫu lại
        self._resample_input_buffer = deque()
        self._resample_output_buffer = deque()

        self._device_input_frame_size = None
        self._is_closing = False

        # Đối tượng luồng âm thanh
        self.input_stream = None  # Luồng ghi âm
        self.output_stream = None  # Luồng phát lại

        # Hàng đợi: Phát hiện từ đánh thức và bộ đệm phát
        self._wakeword_buffer = asyncio.Queue(maxsize=100)
        self._output_buffer = asyncio.Queue(maxsize=500)

        # Callback mã hóa thời gian thực (gửi trực tiếp, không qua hàng đợi)
        self._encoded_audio_callback = None

        # Bộ xử lý AEC
        self.aec_processor = AECProcessor()
        self._aec_enabled = False
        
        # Debug logging
        self._last_log_time = 0

    # -----------------------
    # Phương thức hỗ trợ tự động chọn thiết bị
    # -----------------------
    def _auto_pick_device(self, kind: str) -> Optional[int]:
        """
        Tự động chọn chỉ mục thiết bị ổn định (ưu tiên WASAPI).
        kind: 'input' hoặc 'output'
        """
        assert kind in ("input", "output")
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
        except Exception as e:
            logger.warning(f"Liệt kê thiết bị thất bại: {e}")
            return None

        # 1) Ưu tiên sử dụng thiết bị mặc định của WASAPI HostAPI (nếu có)
        wasapi_index = None
        for idx, ha in enumerate(hostapis):
            name = ha.get("name", "")
            if "WASAPI" in name:
                key = (
                    "default_input_device"
                    if kind == "input"
                    else "default_output_device"
                )
                cand = ha.get(key, -1)
                if isinstance(cand, int) and 0 <= cand < len(devices):
                    d = devices[cand]
                    if (kind == "input" and d["max_input_channels"] > 0) or (
                        kind == "output" and d["max_output_channels"] > 0
                    ):
                        wasapi_index = cand
                        break
        if wasapi_index is not None:
            return wasapi_index

        # 2) Phương án thay thế: Khớp tên trả về từ mặc định hệ thống (kind) + ưu tiên WASAPI
        try:
            default_info = sd.query_devices(kind=kind)  # Không kích hoạt -1
            default_name = default_info.get("name")
        except Exception:
            default_name = None

        scored = []
        for i, d in enumerate(devices):
            if kind == "input":
                ok = d["max_input_channels"] > 0
            else:
                ok = d["max_output_channels"] > 0
            if not ok:
                continue
            host_name = hostapis[d["hostapi"]]["name"]
            score = 0
            if "WASAPI" in host_name:
                score += 5
            if default_name and d["name"] == default_name:
                score += 10
            # Điểm cộng nhỏ: Từ khóa điểm cuối khả dụng phổ biến
            if any(
                k in d["name"]
                for k in [
                    "Speaker",
                    "Loa",
                    "扬声器",
                    "Realtek",
                    "USB",
                    "AMD",
                    "HDMI",
                    "Monitor",
                ]
            ):
                score += 1
            scored.append((score, i))

        if scored:
            scored.sort(reverse=True)
            return scored[0][1]

        # 3) Cuối cùng: Thiết bị đầu tiên có kênh
        for i, d in enumerate(devices):
            if (kind == "input" and d["max_input_channels"] > 0) or (
                kind == "output" and d["max_output_channels"] > 0
            ):
                return i
        return None

    async def initialize(self):
        """
        Khởi tạo thiết bị âm thanh.
        """
        try:
            # Hiển thị và chọn thiết bị âm thanh (tự động chọn lần đầu và ghi vào cấu hình; không ghi đè sau đó)
            await self._select_audio_devices()

            # Lấy thông tin mặc định đầu vào/đầu ra an toàn (tránh -1)
            if self.mic_device_id is not None and self.mic_device_id >= 0:
                input_device_info = sd.query_devices(self.mic_device_id)
            else:
                input_device_info = sd.query_devices(kind="input")

            if self.speaker_device_id is not None and self.speaker_device_id >= 0:
                output_device_info = sd.query_devices(self.speaker_device_id)
            else:
                output_device_info = sd.query_devices(kind="output")

            self.device_input_sample_rate = int(input_device_info["default_samplerate"])
            self.device_output_sample_rate = int(
                output_device_info["default_samplerate"]
            )

            frame_duration_sec = AudioConfig.FRAME_DURATION / 1000
            self._device_input_frame_size = int(
                self.device_input_sample_rate * frame_duration_sec
            )

            logger.info(
                f"Tỷ lệ mẫu đầu vào: {self.device_input_sample_rate}Hz, Đầu ra: {self.device_output_sample_rate}Hz"
            )

            await self._create_resamplers()

            # Không thay đổi mặc định toàn cục, để mỗi luồng tự mang device / samplerate
            sd.default.samplerate = None
            sd.default.channels = AudioConfig.CHANNELS
            sd.default.dtype = np.int16

            await self._create_streams()

            # Bộ giải mã Opus
            self.opus_encoder = opuslib.Encoder(
                AudioConfig.INPUT_SAMPLE_RATE,
                AudioConfig.CHANNELS,
                opuslib.APPLICATION_AUDIO,
            )
            self.opus_decoder = opuslib.Decoder(
                AudioConfig.OUTPUT_SAMPLE_RATE, AudioConfig.CHANNELS
            )

            # Khởi tạo bộ xử lý AEC
            try:
                await self.aec_processor.initialize()
                self._aec_enabled = True
                logger.info("Bộ xử lý AEC đã được bật")
            except Exception as e:
                logger.warning(f"Khởi tạo AEC thất bại, sẽ sử dụng âm thanh gốc: {e}")
                self._aec_enabled = False

            logger.info("Khởi tạo âm thanh hoàn tất")
        except Exception as e:
            logger.error(f"Khởi tạo thiết bị âm thanh thất bại: {e}")
            await self.close()
            raise

    async def _create_resamplers(self):
        """
        Tạo bộ lấy mẫu lại. Đầu vào: Tỷ lệ mẫu thiết bị -> 16kHz (để mã hóa). Đầu ra: 24kHz -> Tỷ lệ mẫu thiết bị (để phát).
        """
        # Bộ lấy mẫu lại đầu vào: Tỷ lệ mẫu thiết bị -> 16kHz (để mã hóa)
        if self.device_input_sample_rate != AudioConfig.INPUT_SAMPLE_RATE:
            self.input_resampler = soxr.ResampleStream(
                self.device_input_sample_rate,
                AudioConfig.INPUT_SAMPLE_RATE,
                AudioConfig.CHANNELS,
                dtype="int16",
                quality="QQ",
            )
            logger.info(f"Lấy mẫu lại đầu vào: {self.device_input_sample_rate}Hz -> 16kHz")

        # Bộ lấy mẫu lại đầu ra: 24kHz -> Tỷ lệ mẫu thiết bị
        if self.device_output_sample_rate != AudioConfig.OUTPUT_SAMPLE_RATE:
            self.output_resampler = soxr.ResampleStream(
                AudioConfig.OUTPUT_SAMPLE_RATE,
                self.device_output_sample_rate,
                AudioConfig.CHANNELS,
                dtype="int16",
                quality="QQ",
            )
            logger.info(
                f"Lấy mẫu lại đầu ra: {AudioConfig.OUTPUT_SAMPLE_RATE}Hz -> {self.device_output_sample_rate}Hz"
            )

    async def _select_audio_devices(self):
        """Hiển thị và chọn thiết bị âm thanh.

        Ưu tiên thiết bị trong file cấu hình, nếu không có sẽ tự động chọn và lưu vào cấu hình (chỉ ghi lần đầu, không ghi đè sau đó).
        """
        try:
            audio_config = self.config.get_config("AUDIO_DEVICES", {}) or {}

            # Có cấu hình rõ ràng chưa (quyết định có ghi lại hay không)
            had_cfg_input = "input_device_id" in audio_config
            had_cfg_output = "output_device_id" in audio_config

            input_device_id = audio_config.get("input_device_id")
            output_device_id = audio_config.get("output_device_id")

            devices = sd.query_devices()

            # --- Xác thực thiết bị đầu vào trong cấu hình ---
            if input_device_id is not None:
                try:
                    if isinstance(input_device_id, int) and 0 <= input_device_id < len(
                        devices
                    ):
                        d = devices[input_device_id]
                        if d["max_input_channels"] > 0:
                            self.mic_device_id = input_device_id
                            logger.info(
                                f"Sử dụng thiết bị micro đã cấu hình: [{input_device_id}] {d['name']}"
                            )
                        else:
                            logger.warning(
                                f"Thiết bị cấu hình [{input_device_id}] không hỗ trợ đầu vào, sẽ tự động chọn"
                            )
                            self.mic_device_id = None
                    else:
                        logger.warning(
                            f"ID thiết bị đầu vào cấu hình [{input_device_id}] không hợp lệ, sẽ tự động chọn"
                        )
                        self.mic_device_id = None
                except Exception as e:
                    logger.warning(f"Xác thực thiết bị đầu vào thất bại: {e}, sẽ tự động chọn")
                    self.mic_device_id = None
            else:
                self.mic_device_id = None

            # --- Xác thực thiết bị đầu ra trong cấu hình ---
            if output_device_id is not None:
                try:
                    if isinstance(
                        output_device_id, int
                    ) and 0 <= output_device_id < len(devices):
                        d = devices[output_device_id]
                        if d["max_output_channels"] > 0:
                            self.speaker_device_id = output_device_id
                            logger.info(
                                f"Sử dụng thiết bị loa đã cấu hình: [{output_device_id}] {d['name']}"
                            )
                        else:
                            logger.warning(
                                f"Thiết bị cấu hình [{output_device_id}] không hỗ trợ đầu ra, sẽ tự động chọn"
                            )
                            self.speaker_device_id = None
                    else:
                        logger.warning(
                            f"ID thiết bị đầu ra cấu hình [{output_device_id}] không hợp lệ, sẽ tự động chọn"
                        )
                        self.speaker_device_id = None
                except Exception as e:
                    logger.warning(f"Xác thực thiết bị đầu ra thất bại: {e}, sẽ tự động chọn")
                    self.speaker_device_id = None
            else:
                self.speaker_device_id = None

            # --- Nếu bất kỳ mục nào trống, tự động chọn (chỉ ghi vào cấu hình lần đầu) ---
            picked_input = self.mic_device_id
            picked_output = self.speaker_device_id

            if picked_input is None:
                picked_input = self._auto_pick_device("input")
                if picked_input is not None:
                    self.mic_device_id = picked_input
                    d = devices[picked_input]
                    logger.info(f"Tự động chọn thiết bị micro: [{picked_input}] {d['name']}")
                else:
                    logger.warning(
                        "Không tìm thấy thiết bị đầu vào khả dụng (sẽ sử dụng mặc định hệ thống, không ghi chỉ mục)."
                    )

            if picked_output is None:
                picked_output = self._auto_pick_device("output")
                if picked_output is not None:
                    self.speaker_device_id = picked_output
                    d = devices[picked_output]
                    logger.info(f"Tự động chọn thiết bị loa: [{picked_output}] {d['name']}")
                else:
                    logger.warning(
                        "Không tìm thấy thiết bị đầu ra khả dụng (sẽ sử dụng mặc định hệ thống, không ghi chỉ mục)."
                    )

            # --- Chỉ ghi khi cấu hình ban đầu thiếu mục tương ứng (tránh ghi đè lần thứ hai) ---
            need_write = (not had_cfg_input and picked_input is not None) or (
                not had_cfg_output and picked_output is not None
            )
            if need_write:
                await self._save_default_audio_config(
                    input_device_id=picked_input if not had_cfg_input else None,
                    output_device_id=picked_output if not had_cfg_output else None,
                )

        except Exception as e:
            logger.warning(f"Chọn thiết bị thất bại: {e}, sẽ sử dụng mặc định hệ thống (không ghi vào cấu hình)")
            # Cho phép None, để PortAudio dùng endpoint mặc định
            self.mic_device_id = (
                self.mic_device_id if isinstance(self.mic_device_id, int) else None
            )
            self.speaker_device_id = (
                self.speaker_device_id
                if isinstance(self.speaker_device_id, int)
                else None
            )

    async def _save_default_audio_config(
        self, input_device_id: Optional[int], output_device_id: Optional[int]
    ):
        """
        Lưu cấu hình thiết bị âm thanh mặc định vào tệp cấu hình (chỉ cho các thiết bị không trống được truyền vào; sẽ không ghi đè các trường hiện có).
        """
        try:
            devices = sd.query_devices()
            audio_config_patch = {}

            # Lưu cấu hình thiết bị đầu vào
            if input_device_id is not None and 0 <= input_device_id < len(devices):
                d = devices[input_device_id]
                audio_config_patch.update(
                    {
                        "input_device_id": input_device_id,
                        "input_device_name": d["name"],
                        "input_sample_rate": int(d["default_samplerate"]),
                    }
                )

            # Lưu cấu hình thiết bị đầu ra
            if output_device_id is not None and 0 <= output_device_id < len(devices):
                d = devices[output_device_id]
                audio_config_patch.update(
                    {
                        "output_device_id": output_device_id,
                        "output_device_name": d["name"],
                        "output_sample_rate": int(d["default_samplerate"]),
                    }
                )

            if audio_config_patch:
                # hợp nhất: không ghi đè khóa hiện có
                current = self.config.get_config("AUDIO_DEVICES", {}) or {}
                for k, v in audio_config_patch.items():
                    if k not in current:  # Chỉ ghi khi chưa có
                        current[k] = v
                success = self.config.update_config("AUDIO_DEVICES", current)
                if success:
                    logger.info("Đã ghi thiết bị âm thanh mặc định vào cấu hình (lần đầu).")
                else:
                    logger.warning("Lưu cấu hình thiết bị âm thanh thất bại")
        except Exception as e:
            logger.error(f"Lưu cấu hình thiết bị âm thanh mặc định thất bại: {e}")

    async def _create_streams(self):
        """
        Tạo luồng âm thanh.
        """
        try:
            # Luồng đầu vào micro
            self.input_stream = sd.InputStream(
                device=self.mic_device_id,  # None=mặc định hệ thống; hoặc chỉ mục cố định
                samplerate=self.device_input_sample_rate,
                channels=AudioConfig.CHANNELS,
                dtype=np.int16,
                blocksize=self._device_input_frame_size,
                callback=self._input_callback,
                finished_callback=self._input_finished_callback,
                latency="low",
            )

            # Chọn tỷ lệ mẫu đầu ra dựa trên tỷ lệ mẫu được thiết bị hỗ trợ
            if self.device_output_sample_rate == AudioConfig.OUTPUT_SAMPLE_RATE:
                # Thiết bị hỗ trợ 24kHz, sử dụng trực tiếp
                output_sample_rate = AudioConfig.OUTPUT_SAMPLE_RATE
                device_output_frame_size = AudioConfig.OUTPUT_FRAME_SIZE
            else:
                # Thiết bị không hỗ trợ 24kHz, sử dụng tỷ lệ mẫu mặc định của thiết bị và bật lấy mẫu lại
                output_sample_rate = self.device_output_sample_rate
                device_output_frame_size = int(
                    self.device_output_sample_rate * (AudioConfig.FRAME_DURATION / 1000)
                )

            self.output_stream = sd.OutputStream(
                device=self.speaker_device_id,  # None=mặc định hệ thống; hoặc chỉ mục cố định
                samplerate=output_sample_rate,
                channels=AudioConfig.CHANNELS,
                dtype=np.int16,
                blocksize=device_output_frame_size,
                callback=self._output_callback,
                finished_callback=self._output_finished_callback,
                latency="low",
            )

            self.input_stream.start()
            self.output_stream.start()

            logger.info("Luồng âm thanh đã khởi động")

        except Exception as e:
            logger.error(f"Tạo luồng âm thanh thất bại: {e}")
            raise

    def _input_callback(self, indata, frames, time_info, status):
        """
        Callback ghi âm, driver phần cứng gọi quy trình xử lý: âm thanh gốc -> lấy mẫu lại 16kHz -> mã hóa gửi + phát hiện từ đánh thức.
        """
        if status and "overflow" not in str(status).lower():
            logger.warning(f"Trạng thái luồng đầu vào: {status}")

        if self._is_closing:
            return

        try:
            audio_data = indata.copy().flatten()

            # Lấy mẫu lại về 16kHz (nếu thiết bị không phải 16kHz)
            if self.input_resampler is not None:
                audio_data = self._process_input_resampling(audio_data)
                if audio_data is None:
                    return

            # DEBUG: Check for silence every 3 seconds
            now = time.time()
            if now - self._last_log_time > 3.0:
                self._last_log_time = now
                # if len(audio_data) > 0:
                #     max_val = np.max(np.abs(audio_data))
                #     # logger.info(f"Audio Input Check - Max Amplitude: {max_val} (Type: {audio_data.dtype})")
                # else:
                #     logger.info("Audio Input Check - No data")

            # Áp dụng xử lý AEC (chỉ macOS cần)
            if (
                self._aec_enabled
                and len(audio_data) == AudioConfig.INPUT_FRAME_SIZE
                and self.aec_processor._is_macos
            ):
                try:
                    audio_data = self.aec_processor.process_audio(audio_data)
                except Exception as e:
                    logger.warning(f"Xử lý AEC thất bại, sử dụng âm thanh gốc: {e}")

            # Mã hóa thời gian thực và gửi (không qua hàng đợi, giảm độ trễ)
            if (
                self._encoded_audio_callback
                and len(audio_data) == AudioConfig.INPUT_FRAME_SIZE
            ):
                try:
                    pcm_data = audio_data.astype(np.int16).tobytes()
                    encoded_data = self.opus_encoder.encode(
                        pcm_data, AudioConfig.INPUT_FRAME_SIZE
                    )
                    if encoded_data:
                        self._encoded_audio_callback(encoded_data)
                except Exception as e:
                    logger.warning(f"Mã hóa ghi âm thời gian thực thất bại: {e}")

            # Đồng thời cung cấp cho phát hiện từ đánh thức (qua hàng đợi)
            self._put_audio_data_safe(self._wakeword_buffer, audio_data.copy())

        except Exception as e:
            logger.error(f"Lỗi callback đầu vào: {e}")

    def _process_input_resampling(self, audio_data):
        """
        Lấy mẫu lại đầu vào về 16kHz.
        """
        try:
            resampled_data = self.input_resampler.resample_chunk(audio_data, last=False)
            if len(resampled_data) > 0:
                self._resample_input_buffer.extend(resampled_data.astype(np.int16))

            expected_frame_size = AudioConfig.INPUT_FRAME_SIZE
            if len(self._resample_input_buffer) < expected_frame_size:
                return None

            frame_data = []
            for _ in range(expected_frame_size):
                frame_data.append(self._resample_input_buffer.popleft())

            return np.array(frame_data, dtype=np.int16)

        except Exception as e:
            logger.error(f"Lấy mẫu lại đầu vào thất bại: {e}")
            return None

    def _put_audio_data_safe(self, queue, audio_data):
        """
        Vào hàng đợi an toàn, khi hàng đợi đầy thì loại bỏ dữ liệu cũ nhất.
        """
        try:
            queue.put_nowait(audio_data)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(audio_data)
            except asyncio.QueueEmpty:
                queue.put_nowait(audio_data)

    def _output_callback(self, outdata: np.ndarray, frames: int, time_info, status):
        """
        Callback phát lại, driver phần cứng gọi lấy dữ liệu từ hàng đợi phát xuất ra loa.
        """
        if status:
            if "underflow" not in str(status).lower():
                logger.warning(f"Trạng thái luồng đầu ra: {status}")

        try:
            if self.output_resampler is not None:
                # Cần lấy mẫu lại: 24kHz -> Tỷ lệ mẫu thiết bị
                self._output_callback_with_resample(outdata, frames)
            else:
                # Phát trực tiếp: 24kHz
                self._output_callback_direct(outdata, frames)

        except Exception as e:
            logger.error(f"Lỗi callback đầu ra: {e}")
            outdata.fill(0)

    def _output_callback_direct(self, outdata: np.ndarray, frames: int):
        """
        Phát trực tiếp dữ liệu 24kHz (khi thiết bị hỗ trợ 24kHz)
        """
        try:
            # Lấy dữ liệu âm thanh từ hàng đợi phát
            audio_data = self._output_buffer.get_nowait()

            if len(audio_data) >= frames * AudioConfig.CHANNELS:
                output_frames = audio_data[: frames * AudioConfig.CHANNELS]
                outdata[:] = output_frames.reshape(-1, AudioConfig.CHANNELS)
            else:
                out_len = len(audio_data) // AudioConfig.CHANNELS
                if out_len > 0:
                    outdata[:out_len] = audio_data[
                        : out_len * AudioConfig.CHANNELS
                    ].reshape(-1, AudioConfig.CHANNELS)
                if out_len < frames:
                    outdata[out_len:] = 0

        except asyncio.QueueEmpty:
            # Xuất im lặng khi không có dữ liệu
            outdata.fill(0)

    def _output_callback_with_resample(self, outdata: np.ndarray, frames: int):
        """
        Phát lấy mẫu lại (24kHz -> Tỷ lệ mẫu thiết bị)
        """
        try:
            # Tiếp tục xử lý dữ liệu 24kHz để lấy mẫu lại
            while len(self._resample_output_buffer) < frames * AudioConfig.CHANNELS:
                try:
                    audio_data = self._output_buffer.get_nowait()
                    # Lấy mẫu lại 24kHz -> Tỷ lệ mẫu thiết bị
                    resampled_data = self.output_resampler.resample_chunk(
                        audio_data, last=False
                    )
                    if len(resampled_data) > 0:
                        self._resample_output_buffer.extend(
                            resampled_data.astype(np.int16)
                        )
                except asyncio.QueueEmpty:
                    break

            need = frames * AudioConfig.CHANNELS
            if len(self._resample_output_buffer) >= need:
                frame_data = [
                    self._resample_output_buffer.popleft() for _ in range(need)
                ]
                output_array = np.array(frame_data, dtype=np.int16)
                outdata[:] = output_array.reshape(-1, AudioConfig.CHANNELS)
            else:
                # Xuất im lặng khi không đủ dữ liệu
                outdata.fill(0)

        except Exception as e:
            logger.warning(f"Xuất lấy mẫu lại thất bại: {e}")
            outdata.fill(0)

    def _input_finished_callback(self):
        """
        Đã kết thúc luồng đầu vào.
        """
        logger.info("Đã kết thúc luồng đầu vào")

    def _reference_finished_callback(self):
        """
        Đã kết thúc luồng tín hiệu tham chiếu.
        """
        logger.info("Đã kết thúc luồng tín hiệu tham chiếu")

    def _output_finished_callback(self):
        """
        Đã kết thúc luồng đầu ra.
        """
        logger.info("Đã kết thúc luồng đầu ra")

    async def reinitialize_stream(self, is_input=True):
        """
        Khởi tạo lại luồng âm thanh.
        """
        if self._is_closing:
            return False if is_input else None

        try:
            if is_input:
                if self.input_stream:
                    self.input_stream.stop()
                    self.input_stream.close()

                self.input_stream = sd.InputStream(
                    device=self.mic_device_id,  # <- Sửa lỗi: Mang theo chỉ mục thiết bị, tránh rơi vào endpoint mặc định không ổn định
                    samplerate=self.device_input_sample_rate,
                    channels=AudioConfig.CHANNELS,
                    dtype=np.int16,
                    blocksize=self._device_input_frame_size,
                    callback=self._input_callback,
                    finished_callback=self._input_finished_callback,
                    latency="low",
                )
                self.input_stream.start()
                logger.info("Khởi tạo lại luồng đầu vào thành công")
                return True
            else:
                if self.output_stream:
                    self.output_stream.stop()
                    self.output_stream.close()

                # Chọn tỷ lệ mẫu đầu ra dựa trên tỷ lệ mẫu được thiết bị hỗ trợ
                if self.device_output_sample_rate == AudioConfig.OUTPUT_SAMPLE_RATE:
                    # Thiết bị hỗ trợ 24kHz, sử dụng trực tiếp
                    output_sample_rate = AudioConfig.OUTPUT_SAMPLE_RATE
                    device_output_frame_size = AudioConfig.OUTPUT_FRAME_SIZE
                else:
                    # Thiết bị không hỗ trợ 24kHz, sử dụng tỷ lệ mẫu mặc định của thiết bị và bật lấy mẫu lại
                    output_sample_rate = self.device_output_sample_rate
                    device_output_frame_size = int(
                        self.device_output_sample_rate
                        * (AudioConfig.FRAME_DURATION / 1000)
                    )

                self.output_stream = sd.OutputStream(
                    device=self.speaker_device_id,  # Chỉ định ID thiết bị loa
                    samplerate=output_sample_rate,
                    channels=AudioConfig.CHANNELS,
                    dtype=np.int16,
                    blocksize=device_output_frame_size,
                    callback=self._output_callback,
                    finished_callback=self._output_finished_callback,
                    latency="low",
                )
                self.output_stream.start()
                logger.info("Khởi tạo lại luồng đầu ra thành công")
                return None
        except Exception as e:
            stream_type = "Đầu vào" if is_input else "Đầu ra"
            logger.error(f"Tạo lại luồng {stream_type} thất bại: {e}")
            if is_input:
                return False
            else:
                raise

    async def get_raw_audio_for_detection(self) -> Optional[bytes]:
        """
        Lấy dữ liệu âm thanh từ đánh thức.
        """
        try:
            if self._wakeword_buffer.empty():
                return None

            audio_data = self._wakeword_buffer.get_nowait()

            if hasattr(audio_data, "tobytes"):
                return audio_data.tobytes()
            elif hasattr(audio_data, "astype"):
                return audio_data.astype("int16").tobytes()
            else:
                return audio_data

        except asyncio.QueueEmpty:
            return None
        except Exception as e:
            logger.error(f"Lấy dữ liệu âm thanh từ đánh thức thất bại: {e}")
            return None

    def set_encoded_audio_callback(self, callback):
        """
        Thiết lập callback mã hóa.
        """
        self._encoded_audio_callback = callback

        if callback:
            logger.info("Bật mã hóa thời gian thực")
        else:
            logger.info("Tắt callback mã hóa")

    def is_aec_enabled(self) -> bool:
        """
        Kiểm tra AEC có được bật hay không.
        """
        return self._aec_enabled

    def get_aec_status(self) -> dict:
        """
        Lấy thông tin trạng thái AEC.
        """
        if not self._aec_enabled or not self.aec_processor:
            return {"enabled": False, "reason": "AEC chưa được bật hoặc khởi tạo thất bại"}

        try:
            return {"enabled": True, **self.aec_processor.get_status()}
        except Exception as e:
            return {"enabled": False, "reason": f"Lấy trạng thái thất bại: {e}"}

    def toggle_aec(self, enabled: bool) -> bool:
        """Chuyển đổi trạng thái bật AEC.

        Args:
            enabled: Có bật AEC hay không

        Returns:
            Trạng thái AEC thực tế
        """
        if not self.aec_processor:
            logger.warning("Bộ xử lý AEC chưa khởi tạo, không thể chuyển đổi trạng thái")
            return False

        self._aec_enabled = enabled and self.aec_processor._is_initialized

        if enabled and not self._aec_enabled:
            logger.warning("Không thể bật AEC, bộ xử lý chưa được khởi tạo đúng cách")

        logger.info(f"Trạng thái AEC: {'Bật' if self._aec_enabled else 'Tắt'}")
        return self._aec_enabled

    async def write_audio(self, opus_data: bytes):
        """
        Giải mã âm thanh và phát Dữ liệu Opus nhận từ mạng -> Giải mã 24kHz -> Hàng đợi phát.
        """
        try:
            # Giải mã Opus thành dữ liệu PCM 24kHz
            pcm_data = self.opus_decoder.decode(
                opus_data, AudioConfig.OUTPUT_FRAME_SIZE
            )

            audio_array = np.frombuffer(pcm_data, dtype=np.int16)

            expected_length = AudioConfig.OUTPUT_FRAME_SIZE * AudioConfig.CHANNELS
            if len(audio_array) != expected_length:
                logger.warning(
                    f"Độ dài âm thanh giải mã bất thường: {len(audio_array)}, kỳ vọng: {expected_length}"
                )
                return

            # Đưa vào hàng đợi phát
            self._put_audio_data_safe(self._output_buffer, audio_array)

        except opuslib.OpusError as e:
            logger.warning(f"Giải mã Opus thất bại, bỏ qua khung này: {e}")
        except Exception as e:
            logger.warning(f"Ghi âm thanh thất bại, bỏ qua khung này: {e}")

    async def wait_for_audio_complete(self, timeout=10.0):
        """
        Chờ phát hoàn tất.
        """
        start = time.time()

        while not self._output_buffer.empty() and time.time() - start < timeout:
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.3)

        if not self._output_buffer.empty():
            output_remaining = self._output_buffer.qsize()
            logger.warning(f"Phát âm thanh hết thời gian, hàng đợi còn lại - Đầu ra: {output_remaining} khung")

    async def clear_audio_queue(self):
        """
        Xóa hàng đợi âm thanh.
        """
        cleared_count = 0

        queues_to_clear = [
            self._wakeword_buffer,
            self._output_buffer,
        ]

        for queue in queues_to_clear:
            while not queue.empty():
                try:
                    queue.get_nowait()
                    cleared_count += 1
                except asyncio.QueueEmpty:
                    break

        if self._resample_input_buffer:
            cleared_count += len(self._resample_input_buffer)
            self._resample_input_buffer.clear()

        if self._resample_output_buffer:
            cleared_count += len(self._resample_output_buffer)
            self._resample_output_buffer.clear()

        if cleared_count > 0:
            logger.info(f"Đã xóa hàng đợi âm thanh, bỏ qua {cleared_count} khung dữ liệu âm thanh")

        if cleared_count > 100:
            gc.collect()
            logger.debug("Thực hiện thu gom rác để giải phóng bộ nhớ")

    async def start_streams(self):
        """
        Bắt đầu luồng âm thanh.
        """
        try:
            if self.input_stream and not self.input_stream.active:
                try:
                    self.input_stream.start()
                except Exception as e:
                    logger.warning(f"Lỗi khi bắt đầu luồng đầu vào: {e}")
                    await self.reinitialize_stream(is_input=True)

            if self.output_stream and not self.output_stream.active:
                try:
                    self.output_stream.start()
                except Exception as e:
                    logger.warning(f"Lỗi khi bắt đầu luồng đầu ra: {e}")
                    await self.reinitialize_stream(is_input=False)

            logger.info("Luồng âm thanh đã bắt đầu")
        except Exception as e:
            logger.error(f"Bắt đầu luồng âm thanh thất bại: {e}")

    async def stop_streams(self):
        """
        Dừng luồng âm thanh.
        """
        try:
            if self.input_stream and self.input_stream.active:
                self.input_stream.stop()
        except Exception as e:
            logger.warning(f"Dừng luồng đầu vào thất bại: {e}")

        try:
            if self.output_stream and self.output_stream.active:
                self.output_stream.stop()
        except Exception as e:
            logger.warning(f"Dừng luồng đầu ra thất bại: {e}")

    async def _cleanup_resampler(self, resampler, name):
        """
        清理重采样器 - 刷新缓冲区并释放资源.
        """
        if resampler:
            try:
                # 刷新缓冲区
                if hasattr(resampler, "resample_chunk"):
                    empty_array = np.array([], dtype=np.int16)
                    resampler.resample_chunk(empty_array, last=True)
            except Exception as e:
                logger.warning(f"刷新{name}重采样器缓冲区失败: {e}")

            try:
                # 尝试显式关闭（如果支持）
                if hasattr(resampler, "close"):
                    resampler.close()
                    logger.debug(f"{name}重采样器已关闭")
            except Exception as e:
                logger.warning(f"关闭{name}重采样器失败: {e}")

    async def close(self):
        """关闭音频编解码器.

        正确的销毁顺序：
        1. 设置关闭标志，阻止新的操作
        2. 停止音频流（停止硬件回调）
        3. 等待回调完全结束
        4. 清空所有队列和缓冲区（打破对 resampler 的间接引用）
        5. 清空回调引用
        6. 清理 resampler（刷新 + 关闭）
        7. 置 None + 强制 GC（释放 nanobind 包装的 C++ 对象）
        """
        if self._is_closing:
            return

        self._is_closing = True
        logger.info("开始关闭音频编解码器...")

        try:
            # 1. 停止音频流（停止硬件回调，这是最关键的第一步）
            if self.input_stream:
                try:
                    if self.input_stream.active:
                        self.input_stream.stop()
                    self.input_stream.close()
                except Exception as e:
                    logger.warning(f"关闭输入流失败: {e}")
                finally:
                    self.input_stream = None

            if self.output_stream:
                try:
                    if self.output_stream.active:
                        self.output_stream.stop()
                    self.output_stream.close()
                except Exception as e:
                    logger.warning(f"关闭输出流失败: {e}")
                finally:
                    self.output_stream = None

            # 2. 等待回调完全停止（给正在执行的回调一点时间完成）
            await asyncio.sleep(0.05)

            # 3. 清空回调引用（打破闭包引用链）
            self._encoded_audio_callback = None

            # 4. 清空所有队列和缓冲区（关键！必须在清理 resampler 之前）
            # 这些缓冲区可能间接持有 resampler 处理过的数据或引用
            await self.clear_audio_queue()

            # 清空重采样缓冲区（可能持有 numpy 数组，间接引用 resampler）
            if self._resample_input_buffer:
                self._resample_input_buffer.clear()
            if self._resample_output_buffer:
                self._resample_output_buffer.clear()

            # 5. 第一次 GC，清理队列和缓冲区中的对象
            gc.collect()

            # 6. 清理并释放重采样器（刷新缓冲区 + 显式关闭）
            await self._cleanup_resampler(self.input_resampler, "输入")
            await self._cleanup_resampler(self.output_resampler, "输出")

            # 7. 显式置 None（断开 Python 引用）
            self.input_resampler = None
            self.output_resampler = None

            # 8. 第二次 GC，释放 resampler 对象（触发 nanobind 析构）
            gc.collect()

            # 额外等待，确保 nanobind 有时间完成析构
            await asyncio.sleep(0.01)

            # 9. 关闭 AEC 处理器
            if self.aec_processor:
                try:
                    await self.aec_processor.close()
                except Exception as e:
                    logger.warning(f"关闭AEC处理器失败: {e}")
                finally:
                    self.aec_processor = None

            # 10. 释放编解码器
            self.opus_encoder = None
            self.opus_decoder = None

            # 11. 最后一次 GC，确保所有对象被回收
            gc.collect()

            logger.info("音频资源已完全释放")
        except Exception as e:
            logger.error(f"关闭音频编解码器过程中发生错误: {e}")
        finally:
            self._is_closing = True

    def __del__(self):
        """
        析构函数.
        """
        if not self._is_closing:
            logger.warning("AudioCodec未正确关闭，请调用close()")
