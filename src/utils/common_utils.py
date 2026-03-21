"""
Mô-đun tập hợp các hàm tiện ích chung bao gồm chuyển văn bản thành giọng nói, thao tác trình duyệt, bảng tạm, v.v.
"""

import queue
import re
import shutil
import threading
import time
import tempfile
import os
import uuid
import webbrowser
from typing import Optional

import requests

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# Hàng đợi phát âm thanh toàn cầu và khóa
_audio_queue = queue.Queue()
_audio_lock = threading.Lock()
_audio_worker_thread = None
_audio_worker_running = False
_audio_device_warmed_up = False


def clean_ssml_tags(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def synthesize_text_custom_tts(text_content: str, output_filename: str, voice_gender: str = "FEMALE") -> Optional[str]:
    custom_tts_base_url = "https://tts.imsteve.dev"
    custom_tts_api_key = f"rand-{uuid.uuid4().hex}"

    payload = {
        "input": clean_ssml_tags(text_content),
        "voice": "vi-VN-HoaiMyNeural" if voice_gender == "FEMALE" else "vi-VN-NamMinhNeural",
        "response_format": "mp3",
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {custom_tts_api_key}",
    }

    try:
        response = requests.post(
            f"{custom_tts_base_url}/v1/audio/speech",
            json=payload,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        with open(output_filename, "wb") as file:
            file.write(response.content)
        return output_filename
    except Exception as e:
        logger.error(f"❌ TTS Error: {e}")
        return None


def _play_custom_tts(text: str) -> bool:
    temp_file_path = None
    try:
        import pygame

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
            temp_file_path = temp_file.name

        output_file = synthesize_text_custom_tts(text, temp_file_path, "FEMALE")
        if not output_file:
            return False

        if not pygame.mixer.get_init():
            pygame.mixer.init()

        pygame.mixer.music.load(output_file)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)
        return True
    except Exception as e:
        logger.error(f"Lỗi khi phát custom TTS: {e}")
        return False
    finally:
        if temp_file_path:
            try:
                os.remove(temp_file_path)
            except Exception:
                pass


def _warm_up_audio_device():
    """
    Làm nóng thiết bị âm thanh để tránh bị mất ký tự đầu tiên.
    """
    global _audio_device_warmed_up
    if _audio_device_warmed_up:
        return

    try:
        import platform
        import subprocess

        system = platform.system()

        if system == "Darwin":
            # Sử dụng giọng mặc định thay vì giọng Trung Quốc
            subprocess.run(
                ["say", "Ready"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif system == "Linux" and shutil.which("espeak"):
            # Sử dụng tiếng Anh hoặc mặc định
            subprocess.run(
                ["espeak", "Ready"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif system == "Windows":
            import win32com.client

            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Speak("Ready")

        _audio_device_warmed_up = True
        logger.info("Đã làm nóng thiết bị âm thanh")
    except Exception as e:
        logger.warning(f"Làm nóng thiết bị âm thanh thất bại: {e}")


def _audio_queue_worker():
    """
    Luồng làm việc của hàng đợi âm thanh, đảm bảo âm thanh được phát theo thứ tự và không bị ngắt quãng.
    """

    while _audio_worker_running:
        try:
            text = _audio_queue.get(timeout=1)
            if text is None:
                break

            with _audio_lock:
                logger.info(f"Bắt đầu phát âm thanh: {text[:50]}...")
                success = _play_system_tts(text)

                if not success:
                    logger.warning("Hệ thống TTS thất bại, thử phương án dự phòng")
                    import os

                    if os.name == "nt":
                        _play_windows_tts(text, set_chinese_voice=False)
                    else:
                        _play_system_tts(text)

                time.sleep(0.5)  # Tạm dừng sau khi phát xong để tránh bị mất âm cuối

            _audio_queue.task_done()

        except queue.Empty:
            continue
        except Exception as e:
            logger.error(f"Luồng làm việc hàng đợi âm thanh gặp lỗi: {e}")

    logger.info("Luồng làm việc hàng đợi âm thanh đã dừng")


def _ensure_audio_worker():
    """
    Đảm bảo luồng làm việc âm thanh đang chạy.
    """
    global _audio_worker_thread, _audio_worker_running

    if _audio_worker_thread is None or not _audio_worker_thread.is_alive():
        _warm_up_audio_device()
        _audio_worker_running = True
        _audio_worker_thread = threading.Thread(target=_audio_queue_worker, daemon=True)
        _audio_worker_thread.start()
        logger.info("Luồng làm việc hàng đợi âm thanh đã khởi động")


def open_url(url: str) -> bool:
    try:
        success = webbrowser.open(url)
        if success:
            logger.info(f"Đã mở trang web thành công: {url}")
        else:
            logger.warning(f"Không thể mở trang web: {url}")
        return success
    except Exception as e:
        logger.error(f"Lỗi khi mở trang web: {e}")
        return False


def copy_to_clipboard(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        logger.info(f'Văn bản "{text}" đã được sao chép vào bảng tạm')
        return True
    except ImportError:
        logger.warning("Chưa cài đặt mô-đun pyperclip, không thể sao chép vào bảng tạm")
        return False
    except Exception as e:
        logger.error(f"Lỗi khi sao chép vào bảng tạm: {e}")
        return False


def _play_windows_tts(text: str, set_chinese_voice: bool = True) -> bool:
    try:
        import win32com.client
        speaker = win32com.client.Dispatch("SAPI.SpVoice")

        if set_chinese_voice:
            try:
                voices = speaker.GetVoices()
                for i in range(voices.Count):
                    if "Chinese" in voices.Item(i).GetDescription():
                        speaker.Voice = voices.Item(i)
                        break
            except Exception as e:
                logger.warning(f"Lỗi khi thiết lập giọng đọc tiếng Trung: {e}")

        try:
            speaker.Rate = -2
        except Exception:
            pass

        enhanced_text = text + "。 。 。"
        speaker.Speak(enhanced_text)
        logger.info("Đã sử dụng tổng hợp giọng nói Windows để phát văn bản")
        time.sleep(0.5)
        return True
    except ImportError:
        logger.warning("Windows TTS không khả dụng, bỏ qua phát âm thanh")
        return False
    except Exception as e:
        logger.error(f"Lỗi phát Windows TTS: {e}")
        return False


def _play_linux_tts(text: str) -> bool:
    import subprocess

    if shutil.which("espeak"):
        try:
            enhanced_text = text + "。 。 。"
            result = subprocess.run(
                ["espeak", "-v", "zh", "-s", "150", "-g", "10", enhanced_text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            time.sleep(0.5)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("espeak phát quá thời gian")
            return False
        except Exception as e:
            logger.error(f"Lỗi phát espeak: {e}")
            return False
    else:
        logger.warning("espeak không khả dụng, bỏ qua phát âm thanh")
        return False


def _play_macos_tts(text: str) -> bool:
    import subprocess

    if shutil.which("say"):
        try:
            enhanced_text = text + "。 。 。"
            result = subprocess.run(
                ["say", "-r", "180", enhanced_text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            time.sleep(0.5)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("lệnh say phát quá thời gian")
            return False
        except Exception as e:
            logger.error(f"Lỗi phát lệnh say: {e}")
            return False
    else:
        logger.warning("lệnh say không khả dụng, bỏ qua phát âm thanh")
        return False


def _play_system_tts(text: str) -> bool:
    import platform
    
    if os.name == "nt":
        return _play_custom_tts(text)
    else:
        system = platform.system()
        if system == "Linux":
            return _play_custom_tts(text)
        elif system == "Darwin":
            return _play_custom_tts(text)
        else:
            logger.warning(f"Hệ thống không được hỗ trợ {system}, bỏ qua phát âm thanh")
            return False


def play_audio_nonblocking(text: str) -> None:
    try:
        _ensure_audio_worker()
        _audio_queue.put(text)
        logger.info(f"Đã thêm nhiệm vụ âm thanh vào hàng đợi: {text[:50]}...")
    except Exception as e:
        logger.error(f"Lỗi khi thêm nhiệm vụ âm thanh vào hàng đợi: {e}")

        def audio_worker():
            try:
                _warm_up_audio_device()
                _play_system_tts(text)
            except Exception as e:
                logger.error(f"Lỗi phát âm thanh dự phòng: {e}")

        threading.Thread(target=audio_worker, daemon=True).start()


def extract_verification_code(text: str) -> Optional[str]:
    try:
        import re

        # Danh sách từ khóa liên quan đến kích hoạt
        activation_keywords = [
            "Đăng nhập",
            "Bảng điều khiển",
            "Kích hoạt",
            "Mã xác nhận",
            "Mã xác thực",
            "Liên kết thiết bị",
            "Thêm thiết bị",
            "Nhập mã xác nhận",
            "Nhập",
            "Bảng",
            "xiaozhi-ai-iot.vn",
            "Mã kích hoạt",
        ]

        # Kiểm tra xem văn bản có chứa từ khóa liên quan đến kích hoạt không
        has_activation_keyword = any(keyword in text for keyword in activation_keywords)

        if not has_activation_keyword:
            logger.debug(f"Văn bản không chứa từ khóa kích hoạt, bỏ qua trích xuất mã xác nhận: {text}")
            return None

        # Mẫu khớp mã xác nhận chính xác hơn
        # Khớp mã xác nhận 6 chữ số, có thể có khoảng trắng ngăn cách
        patterns = [
            r"Mã xác nhận[：:]\s*(\d{6})",  # Mã xác nhận: 123456
            r"Nhập mã xác nhận[：:]\s*(\d{6})",  # Nhập mã xác nhận: 123456
            r"Nhập\s*(\d{6})",  # Nhập 123456
            r"Mã xác nhận\s*(\d{6})",  # Mã xác nhận 123456
            r"Mã kích hoạt[：:]\s*(\d{6})",  # Mã kích hoạt: 123456
            r"(\d{6})[，,。.]",  # 123456, hoặc 123456.
            r"[，,。.]\s*(\d{6})",  # , 123456
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                code = match.group(1)
                logger.info(f"Đã trích xuất mã xác nhận từ văn bản: {code}")
                return code

        # Nếu có từ khóa kích hoạt nhưng không khớp mẫu chính xác, thử mẫu chung
        # Nhưng yêu cầu chữ số phải có ngữ cảnh cụ thể
        match = re.search(r"((?:\d\s*){6,})", text)
        if match:
            code = "".join(match.group(1).split())
            # Mã xác nhận phải là 6 chữ số
            if len(code) == 6 and code.isdigit():
                logger.info(f"Đã trích xuất mã xác nhận từ văn bản (chế độ chung): {code}")
                return code

        logger.warning(f"Không tìm thấy mã xác nhận trong văn bản: {text}")
        return None
    except Exception as e:
        logger.error(f"Lỗi khi trích xuất mã xác nhận: {e}")
        return None


def handle_verification_code(text: str) -> None:
    code = extract_verification_code(text)
    if not code:
        return

    copy_to_clipboard(code)
