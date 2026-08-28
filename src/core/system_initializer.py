#!/usr/bin/env python3
"""
Kịch bản kiểm tra quy trình khởi tạo bốn giai đoạn 
Hiển thị công việc phối hợp của chuẩn bị danh tính thiết bị, quản lý cấu hình, và lấy cấu hình OTA 
Quy trình kích hoạt do người dùng tự thực hiện.
"""

import asyncio
import json
from pathlib import Path
from typing import Dict

from src.constants.system import InitializationStage
from src.core.ota import Ota
from src.utils.config_manager import ConfigManager
from src.utils.device_fingerprint import DeviceFingerprint
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class SystemInitializer:
    """Trình khởi tạo hệ thống - Phối hợp bốn giai đoạn"""

    def __init__(self):
        self.device_fingerprint = None
        self.config_manager = None
        self.ota = None
        self.current_stage = None
        self.activation_data = None
        self.activation_status = {
            "local_activated": False,  # Trạng thái kích hoạt cục bộ
            "server_activated": False,  # Trạng thái kích hoạt trên máy chủ
            "status_consistent": True,  # Trạng thái có nhất quán hay không
        }

    async def run_initialization(self) -> Dict:
        """Chạy quy trình khởi tạo hoàn chỉnh.

        Returns:
            Dict: Kết quả khởi tạo, bao gồm trạng thái kích hoạt và có cần giao diện kích hoạt hay không
        """
        logger.info("Bắt đầu quy trình khởi tạo hệ thống")

        try:
            # Giai đoạn 1: Chuẩn bị danh tính thiết bị
            await self.stage_1_device_fingerprint()

            # Giai đoạn 2: Khởi tạo quản lý cấu hình
            await self.stage_2_config_management()

            # Giai đoạn 3: Lấy cấu hình OTA
            await self.stage_3_ota_config()

            # Lấy cấu hình phiên bản kích hoạt
            activation_version = self.config_manager.get_config(
                "SYSTEM_OPTIONS.NETWORK.ACTIVATION_VERSION", "v1"
            )

            logger.info(f"Phiên bản kích hoạt: {activation_version}")

            # Quyết định có cần quy trình kích hoạt hay không dựa trên phiên bản kích hoạt
            if activation_version == "v1":
                # Giao thức v1: Hoàn thành ba giai đoạn đầu và trả về thành công ngay
                logger.info("Giao thức v1: Ba giai đoạn hoàn thành, không cần quy trình kích hoạt")
                return {
                    "success": True,
                    "local_activated": True,
                    "server_activated": True,
                    "status_consistent": True,
                    "need_activation_ui": False,
                    "status_message": "Khởi tạo giao thức v1 hoàn thành",
                    "activation_version": activation_version,
                }
            else:
                # Giao thức v2: Cần phân tích trạng thái kích hoạt
                logger.info("Giao thức v2: Phân tích trạng thái kích hoạt")
                activation_result = self.analyze_activation_status()
                activation_result["activation_version"] = activation_version

                # Quyết định có cần quy trình kích hoạt hay không dựa trên kết quả phân tích
                if activation_result["need_activation_ui"]:
                    logger.info("Cần hiển thị giao diện kích hoạt")
                else:
                    logger.info("Không cần hiển thị giao diện kích hoạt, thiết bị đã được kích hoạt")

                return activation_result

        except Exception as e:
            logger.error(f"Khởi tạo hệ thống thất bại: {e}")
            return {"success": False, "error": str(e), "need_activation_ui": False}

    async def stage_1_device_fingerprint(self):
        """
        Giai đoạn 1: Chuẩn bị danh tính thiết bị.
        """
        self.current_stage = InitializationStage.DEVICE_FINGERPRINT
        logger.info(f"Bắt đầu {self.current_stage.value}")

        # Khởi tạo dấu vân tay thiết bị
        self.device_fingerprint = DeviceFingerprint.get_instance()

        # Đảm bảo thông tin danh tính thiết bị đầy đủ
        (
            serial_number,
            hmac_key,
            is_activated,
        ) = self.device_fingerprint.ensure_device_identity()

        # Ghi lại trạng thái kích hoạt cục bộ
        self.activation_status["local_activated"] = is_activated

        # Lấy địa chỉ MAC và đảm bảo định dạng chữ thường
        mac_address = self.device_fingerprint.get_mac_address_from_efuse()

        logger.info(f"Số sê-ri thiết bị: {serial_number}")
        logger.info(f"Địa chỉ MAC: {mac_address}")
        logger.info(f"Khóa HMAC: {hmac_key[:8] if hmac_key else None}...")
        logger.info(f"Trạng thái kích hoạt cục bộ: {'Đã kích hoạt' if is_activated else 'Chưa kích hoạt'}")

        # Xác minh tệp efuse.json có đầy đủ không
        efuse_file = Path("config/efuse.json")
        if efuse_file.exists():
            logger.info(f"Vị trí tệp efuse.json: {efuse_file.absolute()}")
            with open(efuse_file, "r", encoding="utf-8") as f:
                efuse_data = json.load(f)
            logger.debug(
                f"Nội dung efuse.json: "
                f"{json.dumps(efuse_data, indent=2, ensure_ascii=False)}"
            )
        else:
            logger.warning("Tệp efuse.json không tồn tại")

        logger.info(f"Hoàn thành {self.current_stage.value}")

    async def stage_2_config_management(self):
        """
        Giai đoạn 2: Khởi tạo quản lý cấu hình.
        """
        self.current_stage = InitializationStage.CONFIG_MANAGEMENT
        logger.info(f"Bắt đầu {self.current_stage.value}")

        # Khởi tạo quản lý cấu hình
        self.config_manager = ConfigManager.get_instance()

        # Đảm bảo CLIENT_ID tồn tại
        self.config_manager.initialize_client_id()

        # Khởi tạo DEVICE_ID từ dấu vân tay thiết bị
        self.config_manager.initialize_device_id_from_fingerprint(
            self.device_fingerprint
        )

        # Xác minh cấu hình quan trọng
        client_id = self.config_manager.get_config("SYSTEM_OPTIONS.CLIENT_ID")
        device_id = self.config_manager.get_config("SYSTEM_OPTIONS.DEVICE_ID")

        logger.info(f"Client ID: {client_id}")
        logger.info(f"Device ID: {device_id}")

        logger.info(f"Hoàn thành {self.current_stage.value}")

    async def stage_3_ota_config(self):
        """
        Giai đoạn 3: Lấy cấu hình OTA.
        """
        self.current_stage = InitializationStage.OTA_CONFIG
        logger.info(f"Bắt đầu {self.current_stage.value}")

        # Khởi tạo OTA
        self.ota = await Ota.get_instance()

        # Lấy và cập nhật cấu hình
        try:
            config_result = await self.ota.fetch_and_update_config()

            logger.info("Kết quả lấy cấu hình OTA:")
            mqtt_status = "Đã lấy" if config_result["mqtt_config"] else "Chưa lấy"
            logger.info(f"- Cấu hình MQTT: {mqtt_status}")

            ws_status = "Đã lấy" if config_result["websocket_config"] else "Chưa lấy"
            logger.info(f"- Cấu hình WebSocket: {ws_status}")

            # Hiển thị tóm tắt thông tin cấu hình đã lấy
            response_data = config_result["response_data"]
            # Thông tin chi tiết chỉ hiển thị trong chế độ gỡ lỗi
            logger.debug(
                f"Dữ liệu phản hồi OTA: {json.dumps(response_data, indent=2, ensure_ascii=False)}"
            )

            if "websocket" in response_data:
                ws_info = response_data["websocket"]
                logger.info(f"WebSocket URL: {ws_info.get('url', 'N/A')}")

            # Kiểm tra xem có thông tin kích hoạt hay không
            if "activation" in response_data:
                logger.info("Phát hiện thông tin kích hoạt, thiết bị cần được kích hoạt")
                self.activation_data = response_data["activation"]
                # Máy chủ cho rằng thiết bị chưa được kích hoạt
                self.activation_status["server_activated"] = False
            else:
                logger.info("Không phát hiện thông tin kích hoạt, thiết bị có thể đã được kích hoạt")
                self.activation_data = None
                # Máy chủ cho rằng thiết bị đã được kích hoạt
                self.activation_status["server_activated"] = True

        except Exception as e:
            logger.error(f"Lấy cấu hình OTA thất bại: {e}")
            raise

        logger.info(f"Hoàn thành {self.current_stage.value}")

    def analyze_activation_status(self) -> Dict:
        """Phân tích trạng thái kích hoạt, quyết định quy trình tiếp theo.

        Returns:
            Dict: Kết quả phân tích, bao gồm có cần giao diện kích hoạt hay không và thông tin khác
        """
        local_activated = self.activation_status["local_activated"]
        server_activated = self.activation_status["server_activated"]

        # Kiểm tra trạng thái có nhất quán hay không
        status_consistent = local_activated == server_activated
        self.activation_status["status_consistent"] = status_consistent

        result = {
            "success": True,
            "local_activated": local_activated,
            "server_activated": server_activated,
            "status_consistent": status_consistent,
            "need_activation_ui": False,
            "status_message": "",
        }

        # Tình huống 1: Cục bộ chưa kích hoạt, máy chủ trả về dữ liệu kích hoạt - Quy trình kích hoạt bình thường
        if not local_activated and not server_activated:
            result["need_activation_ui"] = True
            result["status_message"] = "Thiết bị cần được kích hoạt"

        # Tình huống 2: Cục bộ đã kích hoạt, máy chủ không có dữ liệu kích hoạt - Trạng thái đã kích hoạt bình thường
        elif local_activated and server_activated:
            result["need_activation_ui"] = False
            result["status_message"] = "Thiết bị đã được kích hoạt"

        # Tình huống 3: Cục bộ chưa kích hoạt, nhưng máy chủ không có dữ liệu kích hoạt - Trạng thái không nhất quán, tự động sửa chữa
        elif not local_activated and server_activated:
            logger.warning(
                "Trạng thái không nhất quán: Cục bộ chưa kích hoạt, nhưng máy chủ cho rằng đã kích hoạt, tự động sửa chữa trạng thái cục bộ"
            )
            # Tự động cập nhật trạng thái cục bộ thành đã kích hoạt
            self.device_fingerprint.set_activation_status(True)
            result["need_activation_ui"] = False
            result["status_message"] = "Đã tự động sửa chữa trạng thái kích hoạt"
            result["local_activated"] = True  # Cập nhật trạng thái trong kết quả

        # Tình huống 4: Cục bộ đã kích hoạt, nhưng máy chủ trả về dữ liệu kích hoạt - Trạng thái không nhất quán, cố gắng tự động sửa chữa
        elif local_activated and not server_activated:
            logger.warning("Trạng thái không nhất quán: Cục bộ đã kích hoạt, nhưng máy chủ cho rằng chưa kích hoạt, cố gắng tự động sửa chữa")

            # Kiểm tra xem có dữ liệu kích hoạt hay không
            if self.activation_data and isinstance(self.activation_data, dict):
                # Nếu có mã kích hoạt, cần kích hoạt lại
                if "code" in self.activation_data:
                    logger.info("Máy chủ đã trả về mã kích hoạt, cần kích hoạt lại")
                    result["need_activation_ui"] = True
                    result["status_message"] = "Trạng thái kích hoạt không nhất quán, cần kích hoạt lại"
                else:
                    # Không có mã kích hoạt, có thể là trạng thái máy chủ chưa được cập nhật, cố gắng tiếp tục sử dụng
                    logger.info("Máy chủ không trả về mã kích hoạt, giữ trạng thái cục bộ đã kích hoạt")
                    result["need_activation_ui"] = False
                    result["status_message"] = "Giữ trạng thái cục bộ đã kích hoạt"
            else:
                # Không có dữ liệu kích hoạt, có thể là sự cố mạng, giữ trạng thái cục bộ
                logger.info("Không lấy được dữ liệu kích hoạt, giữ trạng thái cục bộ đã kích hoạt")
                result["need_activation_ui"] = False
                result["status_message"] = "Giữ trạng thái cục bộ đã kích hoạt"
                # Cưỡng chế cập nhật trạng thái nhất quán, tránh kích hoạt lại
                result["status_consistent"] = True
                self.activation_status["status_consistent"] = True
                self.activation_status["server_activated"] = True

        return result

    def get_activation_data(self):
        """
        Lấy dữ liệu kích hoạt (để sử dụng trong mô-đun kích hoạt)
        """
        return getattr(self, "activation_data", None)

    def get_device_fingerprint(self):
        """
        Lấy thể hiện dấu vân tay thiết bị.
        """
        return self.device_fingerprint

    def get_config_manager(self):
        """
        Lấy thể hiện quản lý cấu hình.
        """
        return self.config_manager

    def get_activation_status(self) -> Dict:
        """
        Lấy thông tin trạng thái kích hoạt.
        """
        return self.activation_status

    async def handle_activation_process(self, mode: str = "cli") -> Dict:
        """Xử lý quy trình kích hoạt, tạo giao diện kích hoạt nếu cần.

        Args:
            mode: Chế độ giao diện, "gui" hoặc "cli"

        Returns:
            Dict: Kết quả kích hoạt
        """
        # Chạy quy trình khởi tạo trước
        init_result = await self.run_initialization()

        # Nếu không cần giao diện kích hoạt, trả về kết quả ngay
        if not init_result.get("need_activation_ui", False):
            return {
                "is_activated": True,
                "device_fingerprint": self.device_fingerprint,
                "config_manager": self.config_manager,
            }

        # Cần giao diện kích hoạt, tạo theo chế độ
        return await self._run_cli_activation()

    async def _run_cli_activation(self) -> Dict:
        """Chạy quy trình kích hoạt CLI.

        Returns:
            Dict: Kết quả kích hoạt
        """
        try:
            from src.views.activation.cli_activation import CLIActivation

            # Tạo trình xử lý kích hoạt CLI
            cli_activation = CLIActivation(self)

            # Chạy quy trình kích hoạt
            activation_success = await cli_activation.run_activation_process()

            return {
                "is_activated": activation_success,
                "device_fingerprint": self.device_fingerprint,
                "config_manager": self.config_manager,
            }

        except Exception as e:
            logger.error(f"Quy trình kích hoạt CLI xảy ra ngoại lệ: {e}", exc_info=True)
            return {"is_activated": False, "error": str(e)}
