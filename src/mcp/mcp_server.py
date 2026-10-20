"""
MCP Server Implementation for Python
Reference: https://modelcontextprotocol.io/specification/2024-11-05
"""

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from src.constants.system import SystemConstants
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# Loại giá trị trả về
ReturnValue = Union[bool, int, str]


class PropertyType(Enum):
    """
    Khai báo loại thuộc tính.
    """

    BOOLEAN = "boolean"
    INTEGER = "integer"
    STRING = "string"


@dataclass
class Property:
    """
    Định nghĩa thuộc tính công cụ MCP.
    """

    name: str
    type: PropertyType
    default_value: Optional[Any] = None
    min_value: Optional[int] = None
    max_value: Optional[int] = None

    @property
    def has_default_value(self) -> bool:
        return self.default_value is not None

    @property
    def has_range(self) -> bool:
        return self.min_value is not None and self.max_value is not None

    def value(self, value: Any) -> Any:
        """
        Xác thực và trả về giá trị.
        """
        if self.type == PropertyType.INTEGER and self.has_range:
            if value < self.min_value:
                raise ValueError(
                    f"Value {value} is below minimum allowed: " f"{self.min_value}"
                )
            if value > self.max_value:
                raise ValueError(
                    f"Value {value} exceeds maximum allowed: " f"{self.max_value}"
                )
        return value

    def to_json(self) -> Dict[str, Any]:
        """
        Chuyển đổi sang định dạng JSON.
        """
        result = {"type": self.type.value}

        if self.has_default_value:
            result["default"] = self.default_value

        if self.type == PropertyType.INTEGER:
            if self.min_value is not None:
                result["minimum"] = self.min_value
            if self.max_value is not None:
                result["maximum"] = self.max_value

        return result


@dataclass
class PropertyList:
    """
    Danh sách thuộc tính.
    """

    properties: List[Property] = field(default_factory=list)

    def __init__(self, properties: Optional[List[Property]] = None):
        """
        Khởi tạo danh sách thuộc tính.
        """
        self.properties = properties or []

    def add_property(self, prop: Property):
        self.properties.append(prop)

    def __getitem__(self, name: str) -> Property:
        for prop in self.properties:
            if prop.name == name:
                return prop
        raise KeyError(f"Property not found: {name}")

    def get_required(self) -> List[str]:
        """
        Lấy danh sách tên thuộc tính bắt buộc.
        """
        return [p.name for p in self.properties if not p.has_default_value]

    def to_json(self) -> Dict[str, Any]:
        """
        Chuyển đổi sang định dạng JSON.
        """
        return {prop.name: prop.to_json() for prop in self.properties}

    def parse_arguments(self, arguments: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Phân tích và xác thực tham số.
        """
        result = {}

        for prop in self.properties:
            if arguments and prop.name in arguments:
                value = arguments[prop.name]
                # Kiểm tra loại
                if prop.type == PropertyType.BOOLEAN and isinstance(value, bool):
                    result[prop.name] = value
                elif prop.type == PropertyType.INTEGER and isinstance(
                    value, (int, float)
                ):
                    result[prop.name] = prop.value(int(value))
                elif prop.type == PropertyType.STRING and isinstance(value, str):
                    result[prop.name] = value
                else:
                    raise ValueError(f"Invalid type for property {prop.name}")
            elif prop.has_default_value:
                result[prop.name] = prop.default_value
            else:
                raise ValueError(f"Missing required argument: {prop.name}")

        return result


@dataclass
class McpTool:
    """
    Định nghĩa công cụ MCP.
    """

    name: str
    description: str
    properties: PropertyList
    callback: Callable[[Dict[str, Any]], ReturnValue]

    def to_json(self) -> Dict[str, Any]:
        """
        Chuyển đổi sang định dạng JSON.
        """
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.properties.to_json(),
                "required": self.properties.get_required(),
            },
        }

    async def call(self, arguments: Dict[str, Any]) -> str:
        """
        Gọi công cụ.
        """
        try:
            # Phân tích tham số
            parsed_args = self.properties.parse_arguments(arguments)

            # Gọi hàm callback
            if asyncio.iscoroutinefunction(self.callback):
                result = await self.callback(parsed_args)
            else:
                result = self.callback(parsed_args)

            # Định dạng giá trị trả về
            if isinstance(result, bool):
                text = "true" if result else "false"
            elif isinstance(result, int):
                text = str(result)
            else:
                text = str(result)

            return json.dumps(
                {"content": [{"type": "text", "text": text}], "isError": False}
            )

        except Exception as e:
            logger.error(f"Error calling tool {self.name}: {e}", exc_info=True)
            return json.dumps(
                {"content": [{"type": "text", "text": str(e)}], "isError": True}
            )


class McpServer:
    """
    Triển khai máy chủ MCP.
    """

    _instance = None

    @classmethod
    def get_instance(cls):
        """
        Lấy instance singleton.
        """
        if cls._instance is None:
            cls._instance = McpServer()
        return cls._instance

    def __init__(self):
        self.tools: List[McpTool] = []
        self._send_callback: Optional[Callable] = None
        self._camera = None

    def set_send_callback(self, callback: Callable):
        """
        Thiết lập callback gửi tin nhắn.
        """
        self._send_callback = callback

    def add_tool(self, tool: Union[McpTool, Tuple[str, str, PropertyList, Callable]]):
        """
        Thêm công cụ.
        """
        if isinstance(tool, tuple):
            # Tạo McpTool từ tham số
            name, description, properties, callback = tool
            tool = McpTool(name, description, properties, callback)

        # Kiểm tra xem đã tồn tại chưa
        if any(t.name == tool.name for t in self.tools):
            logger.warning(f"Tool {tool.name} already added")
            return

        logger.info(f"Add tool: {tool.name}")
        self.tools.append(tool)

    def add_common_tools(self):
        """
        Thêm các công cụ phổ biến.
        """
        # Sao lưu danh sách công cụ ban đầu
        original_tools = self.tools.copy()
        self.tools.clear()

        # Thêm công cụ hệ thống
        from src.mcp.tools.system import get_system_tools_manager

        system_manager = get_system_tools_manager()
        system_manager.init_tools(self.add_tool, PropertyList, Property, PropertyType)

        # Thêm công cụ quản lý lịch trình
        from src.mcp.tools.calendar import get_calendar_manager

        calendar_manager = get_calendar_manager()
        calendar_manager.init_tools(self.add_tool, PropertyList, Property, PropertyType)

        # Thêm công cụ đếm ngược
        from src.mcp.tools.timer import get_timer_manager

        timer_manager = get_timer_manager()
        timer_manager.init_tools(self.add_tool, PropertyList, Property, PropertyType)

        # Thêm công cụ YouTube Music mới. Các tool music_player cũ không còn đăng ký.
        from src.mcp.tools.youtube import get_youtube_tools_manager

        youtube_manager = get_youtube_tools_manager()
        youtube_manager.init_tools(self.add_tool, PropertyList, Property, PropertyType)

        # Thêm công cụ SoundCloud
        from src.mcp.tools.soundcloud import get_soundcloud_tools_manager

        soundcloud_manager = get_soundcloud_tools_manager()
        soundcloud_manager.init_tools(self.add_tool, PropertyList, Property, PropertyType)

        # Thêm công cụ camera
        from src.mcp.tools.camera import take_photo

        # Đăng ký công cụ take_photo
        properties = PropertyList([Property("question", PropertyType.STRING)])
        VISION_DESC = (
            "【Hình ảnh/Nhận dạng/OCR/Hỏi đáp】Khi người dùng đề cập đến: chụp ảnh, nhận dạng hình ảnh, đọc/trích xuất văn bản, OCR, dịch văn bản hình ảnh, "
            "xem hình ảnh/ảnh chụp màn hình này, đây là gì, đếm xem, nhận dạng mã QR/mã vạch, so sánh hai hình ảnh, phân tích cảnh/ảnh chụp màn hình lỗi, "
            "trích xuất thông tin bảng/hóa đơn, hỏi đáp hình ảnh thì gọi công cụ này."
            "Chức năng: ①Chụp ảnh hoặc nhận hình ảnh/ảnh chụp màn hình/URL có sẵn; ②Nhận dạng vật thể/cảnh/nhãn; ③OCR (đa ngôn ngữ) và dịch; ④Đếm/vị trí; "
            "⑤Đọc mã QR/mã vạch; ⑥Trích xuất thông tin chính (bảng/hóa đơn); ⑦So sánh hai hình; ⑧Trả lời câu hỏi dựa trên hình ảnh."
            "Gợi ý đầu vào: { mode:'capture'|'upload'|'url', image?, url?, question?, target_lang? }; "
            "Nếu người dùng không cung cấp hình ảnh và cho phép, có thể kích hoạt chụp ảnh (mode='capture'). "
            "Tránh: Hỏi đáp kiến thức thuần văn bản, yêu cầu không liên quan đến hình ảnh."
            "English: Vision/OCR/QA tool. Use when the user provides or asks about a photo/screenshot/image: "
            "describe, classify, OCR, translate, count objects, read QR/barcodes, extract tables/receipts, "
            "compare two images, image QA. Inputs as above. Do NOT use for pure text queries."
            "Examples: 'Bức ảnh này là gì', 'OCR hóa đơn này và dịch sang tiếng Anh', 'Đếm xem có bao nhiêu con mèo trong hình', 'Đọc mã QR này', "
            "'So sánh sự khác biệt giữa hai ảnh chụp màn hình UI này', 'Trích xuất bảng trong ảnh chụp màn hình thành CSV'."
        )

        self.add_tool(
            McpTool(
                "take_photo",  # Giữ nguyên tên để tương thích
                VISION_DESC,
                properties,
                take_photo,
            )
        )

        # Thêm công cụ chụp ảnh màn hình desktop
        from src.mcp.tools.screenshot import take_screenshot

        # Đăng ký công cụ take_screenshot
        screenshot_properties = PropertyList(
            [
                Property("question", PropertyType.STRING),
                Property("display", PropertyType.STRING, default_value=None),
            ]
        )
        SCREENSHOT_DESC = (
            "【Chụp màn hình/Phân tích màn hình】Khi người dùng đề cập đến: chụp màn hình, phân tích màn hình, trên bàn làm việc có gì, "
            "ảnh chụp màn hình, xem giao diện hiện tại, phân tích trang hiện tại, đọc nội dung màn hình, OCR màn hình thì gọi công cụ này. "
            "Chức năng: ①Chụp toàn bộ màn hình desktop; ②Nhận dạng và phân tích nội dung màn hình; ③Trích xuất văn bản OCR màn hình; ④Phân tích phần tử giao diện; "
            "⑤Nhận dạng ứng dụng; ⑥Phân tích ảnh chụp màn hình lỗi; ⑦Kiểm tra trạng thái desktop; ⑧Chụp ảnh nhiều màn hình. "
            "Thông số: { question: 'Câu hỏi của bạn về desktop/màn hình', display: 'Lựa chọn màn hình (tùy chọn)' }; "
            "Giá trị display: 'main'/'chính'/'laptop'(màn hình chính), 'secondary'/'phụ'/'ngoài'(màn hình phụ), hoặc để trống (tất cả màn hình); "
            "Tình huống áp dụng: Chụp ảnh màn hình desktop, phân tích màn hình, chẩn đoán vấn đề giao diện, xem trạng thái ứng dụng, phân tích ảnh chụp màn hình lỗi, v.v. "
            "Lưu ý: Công cụ này sẽ chụp ảnh desktop, vui lòng đảm bảo người dùng đồng ý thao tác chụp ảnh. "
            "English: Desktop screenshot/screen analysis tool. Use when user mentions: screenshot, screen capture, "
            "desktop analysis, screen content, current interface, screen OCR, etc. "
            "Functions: ①Full desktop capture; ②Screen content recognition; ③Screen OCR; ④Interface analysis; "
            "⑤Application identification; ⑥Error screenshot analysis; ⑦Desktop status check. "
            "Parameters: { question: 'Question about desktop/screen', display: 'Display selection (optional)' }; "
            "Display options: 'main'(primary), 'secondary'(external), or empty(all displays). "
            "Examples: 'Chụp ảnh màn hình chính', 'Xem màn hình phụ có gì', 'Phân tích nội dung màn hình hiện tại', 'Đọc văn bản trên màn hình'."
        )

        self.add_tool(
            McpTool(
                "take_screenshot",
                SCREENSHOT_DESC,
                screenshot_properties,
                take_screenshot,
            )
        )

        # Thêm công cụ Bát tự (Mệnh lý)
        from src.mcp.tools.bazi import get_bazi_manager

        bazi_manager = get_bazi_manager()
        bazi_manager.init_tools(self.add_tool, PropertyList, Property, PropertyType)

        # Khôi phục các công cụ cũ
        self.tools.extend(original_tools)

    async def parse_message(self, message: Union[str, Dict[str, Any]]):
        """
        Phân tích tin nhắn MCP.
        """
        try:
            if isinstance(message, str):
                data = json.loads(message)
            else:
                data = message

            logger.info(
                f"[MCP] Phân tích tin nhắn: {json.dumps(data, ensure_ascii=False, indent=2)}"
            )

            # Kiểm tra phiên bản JSONRPC
            if data.get("jsonrpc") != "2.0":
                logger.error(f"Invalid JSONRPC version: {data.get('jsonrpc')}")
                return

            method = data.get("method")
            if not method:
                logger.error("Missing method")
                return

            # Bỏ qua thông báo
            if method.startswith("notifications"):
                logger.info(f"[MCP] Bỏ qua tin nhắn thông báo: {method}")
                return

            params = data.get("params", {})
            id = data.get("id")

            if id is None:
                logger.error(f"Invalid id for method: {method}")
                return

            logger.info(f"[MCP] Xử lý phương thức: {method}, ID: {id}, Tham số: {params}")

            # Xử lý các phương thức khác nhau
            if method == "initialize":
                await self._handle_initialize(id, params)
            elif method == "tools/list":
                await self._handle_tools_list(id, params)
            elif method == "tools/call":
                await self._handle_tool_call(id, params)
            else:
                logger.error(f"Method not implemented: {method}")
                await self._reply_error(id, f"Method not implemented: {method}")

        except Exception as e:
            logger.error(f"Error parsing MCP message: {e}", exc_info=True)
            if "id" in locals():
                await self._reply_error(id, str(e))

    async def _handle_initialize(self, id: int, params: Dict[str, Any]):
        """
        Xử lý yêu cầu khởi tạo.
        """
        # Phân tích capabilities
        capabilities = params.get("capabilities", {})
        await self._parse_capabilities(capabilities)

        # Trả về thông tin máy chủ
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": SystemConstants.APP_NAME,
                "version": SystemConstants.APP_VERSION,
            },
        }

        await self._reply_result(id, result)

    async def _handle_tools_list(self, id: int, params: Dict[str, Any]):
        """
        Xử lý yêu cầu danh sách công cụ.
        """
        cursor = params.get("cursor", "")
        max_payload_size = 8000

        tools_json = []
        total_size = 0
        found_cursor = not cursor
        next_cursor = ""

        for tool in self.tools:
            # Nếu chưa tìm thấy vị trí bắt đầu, tiếp tục tìm kiếm
            if not found_cursor:
                if tool.name == cursor:
                    found_cursor = True
                else:
                    continue

            # Kiểm tra kích thước
            tool_json = tool.to_json()
            tool_size = len(json.dumps(tool_json))

            if total_size + tool_size + 100 > max_payload_size:
                next_cursor = tool.name
                break

            tools_json.append(tool_json)
            total_size += tool_size

        result = {"tools": tools_json}
        if next_cursor:
            result["nextCursor"] = next_cursor

        await self._reply_result(id, result)

    async def _handle_tool_call(self, id: int, params: Dict[str, Any]):
        """
        Xử lý yêu cầu gọi công cụ.
        """
        logger.info(f"[MCP] Nhận yêu cầu gọi công cụ! ID={id}, Tham số={params}")

        tool_name = params.get("name")
        if not tool_name:
            await self._reply_error(id, "Missing tool name")
            return

        logger.info(f"[MCP] Đang cố gọi công cụ: {tool_name}")

        # Tìm công cụ
        tool = None
        for t in self.tools:
            if t.name == tool_name:
                tool = t
                break

        if not tool:
            await self._reply_error(id, f"Unknown tool: {tool_name}")
            return

        # Lấy tham số
        arguments = params.get("arguments", {})

        logger.info(f"[MCP] Bắt đầu thực thi công cụ {tool_name}, Tham số: {arguments}")

        # Gọi công cụ bất đồng bộ
        try:
            result = await tool.call(arguments)
            logger.info(f"[MCP] Công cụ {tool_name} thực thi thành công, Kết quả: {result}")
            await self._reply_result(id, json.loads(result))
        except Exception as e:
            logger.error(f"[MCP] Công cụ {tool_name} thực thi thất bại: {e}", exc_info=True)
            await self._reply_error(id, str(e))

    async def _parse_capabilities(self, capabilities):
        """
        Phân tích capabilities.
        """
        vision = capabilities.get("vision", {})
        if vision and isinstance(vision, dict):
            url = vision.get("url")
            token = vision.get("token")
            if url:
                from src.mcp.tools.camera import get_camera_instance

                camera = get_camera_instance()
                if hasattr(camera, "set_explain_url"):
                    camera.set_explain_url(url)
                if token and hasattr(camera, "set_explain_token"):
                    camera.set_explain_token(token)
                logger.info(f"Vision service configured with URL: {url}")

    async def _reply_result(self, id: int, result: Any):
        """
        Gửi phản hồi thành công.
        """
        payload = {"jsonrpc": "2.0", "id": id, "result": result}

        result_len = len(json.dumps(result))
        logger.info(f"[MCP] Gửi phản hồi thành công: ID={id}, Độ dài kết quả={result_len}")

        if self._send_callback:
            await self._send_callback(json.dumps(payload))
        else:
            logger.error("[MCP] Callback gửi chưa được thiết lập!")

    async def _reply_error(self, id: int, message: str):
        """
        Gửi phản hồi lỗi.
        """
        payload = {"jsonrpc": "2.0", "id": id, "error": {"message": message}}

        logger.error(f"[MCP] Gửi phản hồi lỗi: ID={id}, Lỗi={message}")

        if self._send_callback:
            await self._send_callback(json.dumps(payload))
