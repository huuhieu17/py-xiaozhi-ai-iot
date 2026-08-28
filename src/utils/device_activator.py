import asyncio
from typing import Optional
from src.utils.device_fingerprint import DeviceFingerprint
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class DeviceActivator:
    """Trình quản lý kích hoạt thiết bị - phiên bản hoàn toàn bất đồng bộ"""

    def __init__(self, config_manager):
        """
        Khởi tạo trình kích hoạt thiết bị.
        """
        self.logger = get_logger(__name__)
        self.config_manager = config_manager
        # Sử dụng instance device_fingerprint để quản lý danh tính thiết bị
        self.device_fingerprint = DeviceFingerprint.get_instance()
        # Đảm bảo thông tin danh tính thiết bị đã được tạo
        self._ensure_device_identity()

        # Nhiệm vụ kích hoạt hiện tại
        self._activation_task: Optional[asyncio.Task] = None

    def _ensure_device_identity(self):
        """
        Đảm bảo thông tin danh tính thiết bị đã được tạo.
        """
        (
            serial_number,
            hmac_key,
            is_activated,
        ) = self.device_fingerprint.ensure_device_identity()
        self.logger.info(
            f"Thông tin danh tính thiết bị: Số sê-ri: {serial_number}, Trạng thái: {'Đã kích hoạt' if is_activated else 'Chưa kích hoạt'}"
        )

    def cancel_activation(self):
        """
        Hủy quy trình kích hoạt.
        """
        if self._activation_task and not self._activation_task.done():
            self.logger.info("Đang hủy nhiệm vụ kích hoạt")
            self._activation_task.cancel()

    def has_serial_number(self) -> bool:
        """
        Kiểm tra xem có số sê-ri hay không.
        """
        return self.device_fingerprint.has_serial_number()

    def get_serial_number(self) -> str:
        """
        Lấy số sê-ri.
        """
        return self.device_fingerprint.get_serial_number()

    def get_hmac_key(self) -> str:
        """
        Lấy khóa HMAC.
        """
        return self.device_fingerprint.get_hmac_key()

    def set_activation_status(self, status: bool) -> bool:
        """
        Thiết lập trạng thái kích hoạt.
        """
        return self.device_fingerprint.set_activation_status(status)

    def is_activated(self) -> bool:
        """
        Kiểm tra xem thiết bị đã được kích hoạt chưa.
        """
        return self.device_fingerprint.is_activated()

    def generate_hmac(self, challenge: str) -> str:
        """
        Sử dụng khóa HMAC để tạo chữ ký.
        """
        return self.device_fingerprint.generate_hmac(challenge)

    async def process_activation(self) -> bool:
        """
        Init App 
        """
        try:
            text = f"Xin chào!"
            print("\n==================")
            print(text)
            print("==================\n")

            # Phát giọng nói mã xác minh
            try:
                # Phát giọng nói trong luồng không chặn
                from src.utils.common_utils import play_audio_nonblocking

                play_audio_nonblocking(text)
            except Exception as e:
                self.logger.error(f"Phát giọng nói mã xác minh thất bại: {e}")

            # Thử kích hoạt thiết bị (có truyền mã xác minh để phát lại khi retry)
            return await self.activate()

        except asyncio.CancelledError:
            self.logger.info("Quy trình kích hoạt đã bị hủy")
            return False

    async def activate(self) -> bool:
        """Thực hiện quy trình kích hoạt bất đồng bộ.

        Args:
            challenge: Chuỗi challenge gửi từ máy chủ
            code: Mã xác minh, dùng để phát lại khi thử lại

        Returns:
            bool: Kích hoạt có thành công hay không
        """
        try:
            # Ghi lại nhiệm vụ hiện tại
            self.set_activation_status(True)
            return True

        except asyncio.CancelledError:
            self.logger.info("Quy trình kích hoạt bị hủy")
            return False
