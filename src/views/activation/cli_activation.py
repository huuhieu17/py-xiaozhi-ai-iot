# -*- coding: utf-8 -*-
"""
Quy trình kích hoạt thiết bị CLI cung cấp chức năng tương tự như cửa sổ kích hoạt GUI, nhưng sử dụng đầu ra thuần túy trên terminal.
"""

from datetime import datetime
from typing import Optional

from src.core.system_initializer import SystemInitializer
from src.utils.device_activator import DeviceActivator
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class CLIActivation:
    """
    Bộ xử lý kích hoạt thiết bị CLI.
    """

    def __init__(self, system_initializer: Optional[SystemInitializer] = None):
        # Thể hiện thành phần
        self.system_initializer = system_initializer
        self.device_activator: Optional[DeviceActivator] = None

        # Quản lý trạng thái
        self.current_stage = None
        self.activation_data = None
        self.is_activated = False

        self.logger = logger

    async def run_activation_process(self) -> bool:
        """Chạy quy trình kích hoạt CLI đầy đủ.

        Returns:
            bool: Kích hoạt có thành công hay không
        """
        try:
            self._print_header()

            # Nếu đã cung cấp thể hiện SystemInitializer, sử dụng ngay
            if self.system_initializer:
                self._log_and_print("Sử dụng hệ thống đã được khởi tạo")
                self._update_device_info()
                return await self._start_activation_process()
            else:
                # Nếu không, tạo thể hiện mới và chạy khởi tạo
                self._log_and_print("Bắt đầu quy trình khởi tạo hệ thống")
                self.system_initializer = SystemInitializer()

                # Chạy quy trình khởi tạo
                init_result = await self.system_initializer.run_initialization()

                if init_result.get("success", False):
                    self._update_device_info()

                    # Hiển thị thông điệp trạng thái
                    status_message = init_result.get("status_message", "")
                    if status_message:
                        self._log_and_print(status_message)

                    # Kiểm tra xem có cần kích hoạt không
                    if init_result.get("need_activation_ui", True):
                        return await self._start_activation_process()
                    else:
                        # Không cần kích hoạt, hoàn thành ngay
                        self.is_activated = True
                        self._log_and_print("Thiết bị đã được kích hoạt, không cần thao tác thêm")
                        return True
                else:
                    error_msg = init_result.get("error", "Khởi tạo thất bại")
                    self._log_and_print(f"Lỗi: {error_msg}")
                    return False

        except KeyboardInterrupt:
            self._log_and_print("\nNgười dùng đã ngắt quy trình kích hoạt")
            return False
        except Exception as e:
            self.logger.error(f"Có ngoại lệ trong quy trình kích hoạt CLI: {e}", exc_info=True)
            self._log_and_print(f"Có ngoại lệ trong kích hoạt: {e}")
            return False

    def _print_header(self):
        """
        In thông tin đầu của quy trình kích hoạt CLI.
        """
        print("Đang khởi tạo")

    def _update_device_info(self):
        """
        Cập nhật hiển thị thông tin thiết bị.
        """
        if (
            not self.system_initializer
            or not self.system_initializer.device_fingerprint
        ):
            return

        device_fp = self.system_initializer.device_fingerprint

        # Lấy thông tin thiết bị
        serial_number = device_fp.get_serial_number()
        mac_address = device_fp.get_mac_address_from_efuse()

        # Lấy trạng thái kích hoạt
        activation_status = self.system_initializer.get_activation_status()
        local_activated = activation_status.get("local_activated", False)
        server_activated = activation_status.get("server_activated", False)
        status_consistent = activation_status.get("status_consistent", True)

        # Cập nhật trạng thái kích hoạt
        self.is_activated = local_activated

        # Hiển thị thông tin thiết bị
        print("\U0001F4F1 Thông tin thiết bị:")
        print(f"   Số sê-ri: {serial_number if serial_number else '--'}")
        print(f"   Địa chỉ MAC: {mac_address if mac_address else '--'}")

        # Hiển thị trạng thái kích hoạt
        if not status_consistent:
            if local_activated and not server_activated:
                status_text = "Trạng thái không nhất quán (cần kích hoạt lại)"
            else:
                status_text = "Trạng thái không nhất quán (đã tự động sửa chữa)"
        else:
            status_text = "Đã kích hoạt" if local_activated else "Chưa kích hoạt"

        print(f"   Trạng thái kích hoạt: {status_text}")

    async def _start_activation_process(self) -> bool:
        """
        Bắt đầu quy trình kích hoạt.
        """
        try:
            # Lấy dữ liệu kích hoạt
            activation_data = self.system_initializer.get_activation_data()

            if not activation_data:
                self._log_and_print("\nKhông lấy được dữ liệu kích hoạt")
                print("Lỗi: Không lấy được dữ liệu kích hoạt, vui lòng kiểm tra kết nối mạng")
                return False

            self.activation_data = activation_data

            # Khởi tạo thiết bị kích hoạt
            config_manager = self.system_initializer.get_config_manager()
            self.device_activator = DeviceActivator(config_manager)

            # Bắt đầu quy trình kích hoạt
            self._log_and_print("\nBắt đầu quy trình kích hoạt thiết bị...")
            print("Đang kết nối với máy chủ kích hoạt, vui lòng giữ kết nối mạng...")

            activation_success = await self.device_activator.process_activation()

            if activation_success:
                self._log_and_print("\nThiết bị kích hoạt thành công!")
                self._print_activation_success()
                return True
            else:
                self._log_and_print("\nThiết bị kích hoạt thất bại")
                self._print_activation_failure()
                return False

        except Exception as e:
            self.logger.error(f"Có ngoại lệ trong quy trình kích hoạt: {e}", exc_info=True)
            self._log_and_print(f"\nCó ngoại lệ trong kích hoạt: {e}")
            return False

    def _print_activation_success(self):
        """
        In thông tin kích hoạt thành công.
        """
        print("\n" + "=" * 60)
        print("Thiết bị kích hoạt thành công!")
        print("=" * 60)
        print("Thiết bị đã được thêm thành công vào tài khoản của bạn")
        print("Cấu hình đã được cập nhật tự động")
        print("Chuẩn bị khởi động khách hàng AI nhỏ...")
        print("=" * 60)

    def _print_activation_failure(self):
        """
        In thông tin kích hoạt thất bại.
        """
        print("\n" + "=" * 60)
        print("Thiết bị kích hoạt thất bại")
        print("=" * 60)
        print("Nguyên nhân có thể:")
        print("• Kết nối mạng không ổn định")
        print("• Nhập mã xác thực sai hoặc đã hết hạn")
        print("• Máy chủ tạm thời không khả dụng")
        print("\nGiải pháp:")
        print("• Kiểm tra kết nối mạng")
        print("• Chạy lại chương trình để lấy mã xác thực mới")
        print("• Đảm bảo nhập đúng mã xác thực trên trang web")
        print("=" * 60)

    def _log_and_print(self, message: str):
        """
        Ghi lại log và in ra terminal.
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        print(log_message)
        self.logger.info(message)

    def get_activation_result(self) -> dict:
        """
        Lấy kết quả kích hoạt.
        """
        device_fingerprint = None
        config_manager = None

        if self.system_initializer:
            device_fingerprint = self.system_initializer.device_fingerprint
            config_manager = self.system_initializer.config_manager

        return {
            "is_activated": self.is_activated,
            "device_fingerprint": device_fingerprint,
            "config_manager": config_manager,
        }
