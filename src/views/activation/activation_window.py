# -*- coding: utf-8 -*-
"""
Cửa sổ kích hoạt thiết bị hiển thị quy trình kích hoạt, thông tin thiết bị và tiến trình kích hoạt.
"""

from pathlib import Path
import asyncio
from typing import Optional

from PyQt5.QtCore import QSize, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QPainterPath, QRegion
from PyQt5.QtQuickWidgets import QQuickWidget
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget

from src.core.system_initializer import SystemInitializer
from src.utils.device_activator import DeviceActivator
from src.utils.logging_config import get_logger

from ..base.async_mixins import AsyncMixin, AsyncSignalEmitter
from ..base.base_window import BaseWindow
from .activation_model import ActivationModel

logger = get_logger(__name__)


class ActivationWindow(BaseWindow, AsyncMixin):
    """
    Cửa sổ kích hoạt thiết bị.
    """

    # Tín hiệu tùy chỉnh
    activation_completed = pyqtSignal(bool)  # Tín hiệu hoàn thành kích hoạt
    window_closed = pyqtSignal()  # Tín hiệu cửa sổ đã đóng

    def __init__(
        self,
        system_initializer: Optional[SystemInitializer] = None,
        parent: Optional = None,
    ):
        # Liên quan đến QML - Phải tạo trước khi gọi super().__init__()
        self.qml_widget = None
        self.activation_model = ActivationModel()

        super().__init__(parent)

        # Thực thể thành phần
        self.system_initializer = system_initializer
        self.device_activator: Optional[DeviceActivator] = None

        # Quản lý trạng thái
        self.current_stage = None
        self.activation_data = None
        self.is_activated = False
        self.initialization_started = False
        self.status_message = ""

        # Bộ phát tín hiệu bất đồng bộ
        self.signal_emitter = AsyncSignalEmitter()
        self._setup_signal_connections()

        # Liên quan đến kéo cửa sổ
        self.drag_position = None

        # Khởi động chậm quá trình khởi tạo (đợi vòng lặp sự kiện chạy)
        self.start_update_timer(100)  # Bắt đầu khởi tạo sau 100ms

    def _setup_ui(self):
        """
        Thiết lập UI.
        """
        # Thiết lập cửa sổ không viền
        # Kiểm tra loại máy chủ hiển thị để tương thích với Wayland
        import os

        is_wayland = (
            os.environ.get("WAYLAND_DISPLAY")
            or os.environ.get("XDG_SESSION_TYPE") == "wayland"
        )

        if is_wayland:
            # Môi trường Wayland: không sử dụng WindowStaysOnTopHint (không hỗ trợ)
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
            self.logger.info("Phát hiện môi trường Wayland, sử dụng cờ cửa sổ tương thích")
        else:
            # Môi trường X11: sử dụng tính năng đầy đủ
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.logger.info("Phát hiện môi trường X11, sử dụng cờ cửa sổ đầy đủ")

        self.setAttribute(Qt.WA_TranslucentBackground)

        # Tạo widget trung tâm
        central_widget = QWidget()
        central_widget.setStyleSheet("background: transparent;")
        self.setCentralWidget(central_widget)

        # Tạo bố cục
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Tạo widget QML
        self.qml_widget = QQuickWidget()
        self.qml_widget.setResizeMode(QQuickWidget.SizeRootObjectToView)

        # Chỉ sử dụng WA_AlwaysStackOnTop trong môi trường X11 (Wayland không hỗ trợ)
        if not is_wayland:
            self.qml_widget.setAttribute(Qt.WA_AlwaysStackOnTop)

        self.qml_widget.setClearColor(Qt.transparent)

        # Đăng ký mô hình dữ liệu vào ngữ cảnh QML
        qml_context = self.qml_widget.rootContext()
        qml_context.setContextProperty("activationModel", self.activation_model)

        # Tải tệp QML
        try:
            from src.utils.resource_finder import resource_finder
            found = resource_finder.find_file("activation_window.qml")
            qml_file = found if found else Path(__file__).parent / "activation_window.qml"
        except Exception:
            qml_file = Path(__file__).parent / "activation_window.qml"
            
        self.qml_widget.setSource(QUrl.fromLocalFile(str(qml_file)))

        # Kiểm tra xem QML đã tải thành công chưa
        if self.qml_widget.status() == QQuickWidget.Error:
            self.logger.error("Tải QML thất bại, lý do có thể là:")
            for error in self.qml_widget.errors():
                self.logger.error(f"  - {error.toString()}")

            # Trong môi trường Wayland, nếu tải QML thất bại, nhắc người dùng sử dụng chế độ CLI
            if is_wayland:
                self.logger.warning("Tải QML thất bại trong môi trường Wayland, khuyến nghị sử dụng chế độ CLI để kích hoạt")
                self.logger.info("Sử dụng lệnh: python main.py --mode cli")

        # Thêm vào bố cục
        layout.addWidget(self.qml_widget)

        # Thiết lập kích thước tự điều chỉnh
        self._setup_adaptive_size()

        # Thiết lập kết nối chậm, đảm bảo QML đã hoàn toàn tải
        self._setup_qml_connections()

    def _setup_adaptive_size(self):
        """
        Thiết lập kích thước cửa sổ tự điều chỉnh.
        """
        # Lấy kích thước màn hình
        screen = QApplication.primaryScreen()
        screen_size = screen.size()
        screen_width = screen_size.width()
        screen_height = screen_size.height()

        self.logger.info(f"Phát hiện độ phân giải màn hình: {screen_width}x{screen_height}")

        # Chọn kích thước cửa sổ phù hợp dựa trên kích thước màn hình
        if screen_width <= 480 or screen_height <= 320:
            # Màn hình cực nhỏ (ví dụ: 3.5 inch 480x320)
            window_width, window_height = 450, 250
            self.setMinimumSize(QSize(450, 250))
            self._apply_compact_styles()
        elif screen_width <= 800 or screen_height <= 480:
            # Màn hình nhỏ (ví dụ: 7 inch 800x480)
            window_width, window_height = 480, 280
            self.setMinimumSize(QSize(480, 280))
            self._apply_small_screen_styles()
        elif screen_width <= 1024 or screen_height <= 600:
            # Màn hình trung bình
            window_width, window_height = 520, 300
            self.setMinimumSize(QSize(520, 300))
        else:
            # Màn hình lớn (màn hình PC)
            window_width, window_height = 550, 320
            self.setMinimumSize(QSize(550, 320))

        # Đảm bảo cửa sổ không vượt quá kích thước màn hình
        max_width = min(window_width, screen_width - 50)
        max_height = min(window_height, screen_height - 50)

        self.resize(max_width, max_height)

        # Hiển thị ở giữa
        self.move((screen_width - max_width) // 2, (screen_height - max_height) // 2)

        self.logger.info(f"Thiết lập kích thước cửa sổ: {max_width}x{max_height}")

    def _apply_compact_styles(self):
        """Áp dụng kiểu dáng gọn nhẹ - phù hợp với màn hình cực nhỏ"""
        # Điều chỉnh kích thước phông chữ
        self.setStyleSheet(
            """
            QLabel { font-size: 10px; }
            QPushButton { font-size: 10px; padding: 4px 8px; }
            QTextEdit { font-size: 8px; }
        """
        )

    def _apply_small_screen_styles(self):
        """
        Áp dụng kiểu dáng cho màn hình nhỏ.
        """
        # Điều chỉnh kích thước phông chữ
        self.setStyleSheet(
            """
            QLabel { font-size: 11px; }
            QPushButton { font-size: 11px; padding: 6px 10px; }
            QTextEdit { font-size: 9px; }
        """
        )

    def _setup_connections(self):
        """
        Thiết lập kết nối tín hiệu.
        """
        # Kết nối tín hiệu mô hình dữ liệu
        self.activation_model.copyCodeClicked.connect(self._on_copy_code_clicked)
        self.activation_model.retryClicked.connect(self._on_retry_clicked)
        self.activation_model.closeClicked.connect(self.close)

        self.logger.debug("Thiết lập kết nối tín hiệu cơ bản hoàn tất")

    def _setup_qml_connections(self):
        """
        Thiết lập kết nối tín hiệu QML.
        """
        # Kết nối tín hiệu QML với hàm xử lý Python
        if self.qml_widget and self.qml_widget.rootObject():
            root_object = self.qml_widget.rootObject()
            root_object.copyCodeClicked.connect(self._on_copy_code_clicked)
            root_object.retryClicked.connect(self._on_retry_clicked)
            root_object.closeClicked.connect(self.close)
            self.logger.debug("Thiết lập kết nối tín hiệu QML hoàn tất")
        else:
            self.logger.warning("Không tìm thấy đối tượng gốc QML, không thể thiết lập kết nối tín hiệu")

    def _setup_signal_connections(self):
        """
        Thiết lập kết nối tín hiệu bất đồng bộ.
        """
        self.signal_emitter.status_changed.connect(self._on_status_changed)
        self.signal_emitter.error_occurred.connect(self._on_error_occurred)
        self.signal_emitter.data_ready.connect(self._on_data_ready)

    def _on_timer_update(self):
        """Callback cập nhật đồng hồ - khởi động quá trình khởi tạo"""
        if not self.initialization_started:
            self.initialization_started = True
            self.stop_update_timer()  # Dừng đồng hồ

            # Chỉ khởi động khởi tạo khi có bộ khởi tạo hệ thống
            if self.system_initializer is not None:
                # Bây giờ vòng lặp sự kiện nên đang chạy, có thể tạo nhiệm vụ bất đồng bộ
                try:
                    self.create_task(self._start_initialization(), "initialization")
                except RuntimeError as e:
                    self.logger.error(f"Không thể tạo nhiệm vụ khởi tạo: {e}")
                    # Nếu vẫn không thành công, thử lại
                    self.start_update_timer(500)
            else:
                self.logger.info("Không có bộ khởi tạo hệ thống, bỏ qua khởi tạo tự động")

    async def _start_initialization(self):
        """
        Bắt đầu quy trình khởi tạo hệ thống.
        """
        try:
            # Nếu đã cung cấp thể hiện SystemInitializer, sử dụng ngay
            if self.system_initializer:
                self._update_device_info()
                await self._start_activation_process()
            else:
                # Nếu không, tạo thể hiện mới và chạy khởi tạo
                self.system_initializer = SystemInitializer()

                # Chạy quy trình khởi tạo
                init_result = await self.system_initializer.run_initialization()

                if init_result.get("success", False):
                    self._update_device_info()

                    # Hiển thị thông điệp trạng thái
                    self.status_message = init_result.get("status_message", "")
                    if self.status_message:
                        self.signal_emitter.emit_status(self.status_message)

                    # Kiểm tra xem có cần kích hoạt không
                    if init_result.get("need_activation_ui", True):
                        await self._start_activation_process()
                    else:
                        # Không cần kích hoạt, hoàn thành ngay
                        self.is_activated = True
                        self.activation_completed.emit(True)
                else:
                    error_msg = init_result.get("error", "Khởi tạo thất bại")
                    self.signal_emitter.emit_error(error_msg)

        except Exception as e:
            self.logger.error(f"Ngoại lệ trong quá trình khởi tạo: {e}", exc_info=True)
            self.signal_emitter.emit_error(f"Lỗi khởi tạo: {e}")

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

        # Cập nhật số sê-ri
        serial_number = device_fp.get_serial_number()
        self.activation_model.serialNumber = serial_number if serial_number else "--"

        # Cập nhật địa chỉ MAC
        mac_address = device_fp.get_mac_address_from_efuse()
        self.activation_model.macAddress = mac_address if mac_address else "--"

        # Lấy trạng thái kích hoạt
        activation_status = self.system_initializer.get_activation_status()
        local_activated = activation_status.get("local_activated", False)
        server_activated = activation_status.get("server_activated", False)
        status_consistent = activation_status.get("status_consistent", True)

        # Cập nhật hiển thị trạng thái kích hoạt
        self.is_activated = local_activated

        if not status_consistent:
            self.activation_model.set_status_inconsistent(
                local_activated, server_activated
            )
        else:
            if local_activated:
                self.activation_model.set_status_activated()
            else:
                self.activation_model.set_status_not_activated()

        # Khởi tạo hiển thị mã kích hoạt
        self.activation_model.reset_activation_code()

    async def _start_activation_process(self):
        """
        Bắt đầu quy trình kích hoạt.
        """
        self.signal_emitter.emit_status("Đang khởi tạo quy trình kích hoạt...")

        while not self.is_shutdown_requested():
            try:
                # Lấy dữ liệu kích hoạt (tự động tạo lại mã nếu cần)
                activation_data = self.system_initializer.get_activation_data()

                if not activation_data:
                    self.signal_emitter.emit_error("Lỗi kết nối mạng. Đang thử lại sau 5s...")
                    await asyncio.sleep(5)
                    continue

                self.activation_data = activation_data

                # Khởi tạo thiết bị kích hoạt
                config_manager = self.system_initializer.get_config_manager()
                self.device_activator = DeviceActivator(config_manager)

                # Bắt đầu quy trình kích hoạt
                self.signal_emitter.emit_status("Vui lòng nhập mã xác thực trên website...")

                # Hàm này đã có cơ chế thử lại nội bộ (khoảng 5 phút)
                activation_success = await self.device_activator.process_activation()

                # Kiểm tra xem có phải do cửa sổ đóng mà hủy không
                if self.is_shutdown_requested():
                    self.signal_emitter.emit_status("Quy trình kích hoạt đã bị hủy")
                    break

                if activation_success:
                    self.signal_emitter.emit_status(
                        "Kích hoạt thành công. Đang xác nhận với máy chủ..."
                    )

                    confirmed = await self._confirm_server_activation()
                    if confirmed:
                        self.signal_emitter.emit_status(
                            "Máy chủ đã xác nhận kích hoạt. Đang vào màn hình hỏi đáp..."
                        )
                        # Cho người dùng kịp đọc trạng thái
                        await asyncio.sleep(1.5)
                        self._on_activation_success()
                        break  # Thoát vòng lặp khi thành công
                    else:
                        # Không cho phép đóng và vào chat nếu máy chủ chưa xác nhận
                        self.signal_emitter.emit_error(
                            "Máy chủ chưa xác nhận kích hoạt. Vui lòng thử lại hoặc kiểm tra mạng."
                        )
                        await asyncio.sleep(2)
                        continue
                else:
                    self.signal_emitter.emit_status("Hết thời gian chờ mã. Đang tạo mã mới...")
                    await asyncio.sleep(2)  # Chờ một chút trước khi thử lại

            except Exception as e:
                self.logger.error(f"Ngoại lệ trong quy trình kích hoạt: {e}", exc_info=True)
                self.signal_emitter.emit_error(f"Lỗi kích hoạt: {e}. Thử lại sau 5s.")
                await asyncio.sleep(5)

    def _on_activation_success(self):
        """
        Xử lý khi kích hoạt thành công.
        """
        # Cập nhật hiển thị trạng thái
        self.activation_model.set_status_activated()

        # Phát tín hiệu hoàn thành
        self.activation_completed.emit(True)
        self.is_activated = True

    async def _confirm_server_activation(self) -> bool:
        """Xác nhận kích hoạt đã được máy chủ ghi nhận trước khi đóng cửa sổ."""
        if not self.system_initializer:
            return False

        # Thử lại một vài lần vì server có thể cập nhật trạng thái trễ
        for _ in range(12):
            if self.is_shutdown_requested():
                return False
            try:
                await self.system_initializer.stage_3_ota_config()
                status = self.system_initializer.get_activation_status() or {}
                if status.get("server_activated", False):
                    return True
            except Exception as e:
                self.logger.warning(f"Xác nhận kích hoạt thất bại, thử lại: {e}")
            await asyncio.sleep(2)

        return False

    def _on_status_changed(self, status: str):
        """
        Xử lý thay đổi trạng thái.
        """
        self.update_status(status)

    def _on_error_occurred(self, error_message: str):
        """
        Xử lý lỗi.
        """
        self.logger.error(f"Lỗi: {error_message}")
        self.update_status(f"Lỗi: {error_message}")

    def _on_data_ready(self, data):
        """
        Xử lý dữ liệu đã sẵn sàng - Cập nhật thông tin thiết bị.
        """
        self.logger.debug(f"Nhận dữ liệu: {data}")
        if isinstance(data, dict):
            serial = data.get("serial_number")
            mac = data.get("mac_address")
            if serial or mac:
                self.logger.info(f"Cập nhật thông tin thiết bị qua tín hiệu: SN={serial}, MAC={mac}")
                self.activation_model.update_device_info(
                    serial_number=serial, mac_address=mac
                )

    def _on_retry_clicked(self):
        """
        Xử lý nhấp nút chuyển hướng kích hoạt - Mở trang kích hoạt.
        """
        self.logger.info("Người dùng nhấp vào chuyển hướng kích hoạt")

        # Lấy URL kích hoạt từ cấu hình và mở
        try:
            from src.utils.common_utils import open_url
            from src.utils.config_manager import ConfigManager

            config = ConfigManager.get_instance()
            ota_url = config.get_config("SYSTEM_OPTIONS.NETWORK.AUTHORIZATION_URL", "")
            if ota_url:
                open_url(ota_url)
                self.update_status("Đã mở trang kích hoạt, vui lòng nhập mã xác thực trong trình duyệt")
            else:
                self.logger.error("URL kích hoạt không được cấu hình")
                self.update_status("Lỗi: URL kích hoạt không được cấu hình")
        except Exception as e:
            self.logger.error(f"Mở trang kích hoạt thất bại: {e}")
            self.update_status(f"Mở trang kích hoạt thất bại: {e}")

    def _on_copy_code_clicked(self):
        """
        Xử lý nhấp nút sao chép mã xác thực.
        """
        if self.activation_data:
            code = self.activation_data.get("code", "")
            if code:
                clipboard = QApplication.clipboard()
                clipboard.setText(code)
                self.update_status(f"Mã xác thực đã được sao chép vào clipboard: {code}")
        else:
            # Lấy mã kích hoạt từ mô hình
            code = self.activation_model.activationCode
            if code and code != "--":
                clipboard = QApplication.clipboard()
                clipboard.setText(code)
                self.update_status(f"Mã xác thực đã được sao chép vào clipboard: {code}")

    def update_status(self, message: str):
        """
        Cập nhật thông tin trạng thái.
        """
        self.logger.info(message)

        # Nếu có nhãn trạng thái, cập nhật nó
        if hasattr(self, "status_label"):
            self.status_label.setText(message)

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

    async def shutdown_async(self):
        """
        Đóng cửa sổ một cách bất đồng bộ.
        """
        self.logger.info("Đang đóng cửa sổ kích hoạt...")

        # Hủy quy trình kích hoạt (nếu đang tiến hành)
        if self.device_activator:
            self.device_activator.cancel_activation()
            self.logger.info("Đã gửi tín hiệu hủy kích hoạt")

        # Trước tiên, dọn dẹp các nhiệm vụ bất đồng bộ
        await self.cleanup_async_tasks()

        # Sau đó gọi phương thức đóng của lớp cha
        await super().shutdown_async()

    def mousePressEvent(self, event):
        """
        Sự kiện nhấn chuột - dùng để kéo cửa sổ.
        """
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """
        Sự kiện di chuyển chuột - thực hiện kéo cửa sổ.
        """
        if event.buttons() == Qt.LeftButton and self.drag_position:
            self.move(event.globalPos() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        """
        Sự kiện thả chuột.
        """
        self.drag_position = None

    def _apply_native_rounded_corners(self):
        """
        Áp dụng hình dạng cửa sổ góc tròn gốc.
        """
        try:
            # Lấy kích thước cửa sổ
            width = self.width()
            height = self.height()

            # Tạo đường viền góc tròn
            radius = 16  # Bán kính góc tròn
            path = QPainterPath()
            path.addRoundedRect(0, 0, width, height, radius, radius)

            # Tạo vùng và áp dụng vào cửa sổ
            region = QRegion(path.toFillPolygon().toPolygon())
            self.setMask(region)

            self.logger.info(
                f"Đã áp dụng hình dạng cửa sổ góc tròn gốc: {width}x{height}, bán kính góc tròn: {radius}px"
            )

        except Exception as e:
            self.logger.error(f"Áp dụng hình dạng góc tròn gốc thất bại: {e}")

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def closeEvent(self, event):
        """
        Xử lý sự kiện đóng cửa sổ.
        """
        if self.is_activated:
            self.logger.info("Sự kiện đóng cửa sổ kích hoạt (Đã kích hoạt)")
            self.window_closed.emit()
            event.accept()
        else:
            self.logger.info("Sự kiện đóng cửa sổ bị từ chối (Chưa kích hoạt)")
            event.ignore()
