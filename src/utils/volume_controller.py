import platform
import re
import shutil
import subprocess
import threading
from functools import wraps
from typing import Any, Callable, List, Optional

from src.utils.logging_config import get_logger


class VolumeController:
    """
    Bộ điều khiển âm lượng đa nền tảng.
    """

    # Âm lượng mặc định
    DEFAULT_VOLUME = 70

    # Mapping method theo nền tảng
    PLATFORM_INIT = {
        "Windows": "_init_windows",
        "Darwin": "_init_macos",
        "Linux": "_init_linux",
    }

    VOLUME_METHODS = {
        "Windows": ("_get_windows_volume", "_set_windows_volume"),
        "Darwin": ("_get_macos_volume", "_set_macos_volume"),
        "Linux": ("_get_linux_volume", "_set_linux_volume"),
    }

    LINUX_VOLUME_METHODS = {
        "pactl": ("_get_pactl_volume", "_set_pactl_volume"),
        "wpctl": ("_get_wpctl_volume", "_set_wpctl_volume"),
        "amixer": ("_get_amixer_volume", "_set_amixer_volume"),
        "alsamixer": (None, "_set_alsamixer_volume"),
    }

    # Phụ thuộc module theo nền tảng
    PLATFORM_MODULES = {
        "Windows": {
            "pycaw": "pycaw.pycaw",
            "comtypes": "comtypes",
            "ctypes": "ctypes",
        },
        "Darwin": {
            "applescript": "applescript",
        },
        "Linux": {},
    }

    def __init__(self):
        """
        Khởi tạo bộ điều khiển âm lượng.
        """
        self.logger = get_logger("VolumeController")
        self.system = platform.system()
        self.is_arm = platform.machine().startswith(("arm", "aarch"))
        self.linux_tool = None
        self._module_cache = {}  # cache module
        self._windows_com_thread_id: Optional[int] = None

        # Khởi tạo controller theo nền tảng
        init_method_name = self.PLATFORM_INIT.get(self.system)
        if init_method_name:
            init_method = getattr(self, init_method_name)
            init_method()
        else:
            self.logger.warning(f"Hệ điều hành chưa được hỗ trợ: {self.system}")
            raise NotImplementedError(f"Hệ điều hành chưa được hỗ trợ: {self.system}")

    def _lazy_import(self, module_name: str, attr: str = None) -> Any:
        """Lazy-load module, hỗ trợ cache và import attribute.

        Args:
            module_name: Tên module
            attr: (tùy chọn) tên attribute trong module

        Returns:
            Module hoặc attribute đã import
        """
        if module_name in self._module_cache:
            module = self._module_cache[module_name]
        else:
            try:
                module = __import__(
                    module_name, fromlist=["*"] if "." in module_name else []
                )
                self._module_cache[module_name] = module
            except ImportError as e:
                self.logger.warning(f"Import module {module_name} thất bại: {e}")
                raise

        if attr:
            return getattr(module, attr)
        return module

    def _safe_execute(self, func_name: str, default_return: Any = None) -> Callable:
        """
        Decorator chạy hàm an toàn.
        """

        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    self.logger.warning(f"{func_name} thất bại: {e}")
                    return default_return

            return wrapper

        return decorator

    def _run_command(
        self, cmd: List[str], check: bool = False
    ) -> Optional[subprocess.CompletedProcess]:
        """
        Hàm chạy command dùng chung.
        """
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=check)
        except Exception as e:
            self.logger.debug(f"Chạy command thất bại {' '.join(cmd)}: {e}")
            return None

    def _init_windows(self) -> None:
        """
        Khởi tạo điều khiển âm lượng Windows.
        """
        try:
            # asyncio.to_thread có thể chạy mỗi thao tác trên một thread khác.
            # COM và interface âm thanh phải được khởi tạo trong thread đang sử dụng.
            comtypes = self._lazy_import("comtypes")
            comtypes.CoInitialize()
            self._windows_com_thread_id = threading.get_ident()

            # Lazy-load các module cần thiết
            POINTER = self._lazy_import("ctypes", "POINTER")
            cast = self._lazy_import("ctypes", "cast")
            CLSCTX_ALL = self._lazy_import("comtypes", "CLSCTX_ALL")
            AudioUtilities = self._lazy_import("pycaw.pycaw", "AudioUtilities")
            IAudioEndpointVolume = self._lazy_import(
                "pycaw.pycaw", "IAudioEndpointVolume"
            )

            self.devices = AudioUtilities.GetSpeakers()
            endpoint_volume = getattr(self.devices, "EndpointVolume", None)
            if endpoint_volume is not None:
                # pycaw mới đã cung cấp interface trực tiếp trên AudioDevice.
                self.volume_control = endpoint_volume
            else:
                # Tương thích với các phiên bản pycaw cũ.
                interface = self.devices.Activate(
                    IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                )
                self.volume_control = cast(interface, POINTER(IAudioEndpointVolume))
            self.logger.debug("Khởi tạo điều khiển âm lượng Windows thành công")
        except Exception as e:
            self.logger.error(f"Khởi tạo điều khiển âm lượng Windows thất bại: {e}")
            raise

    def _init_macos(self) -> None:
        """
        Khởi tạo điều khiển âm lượng macOS.
        """
        try:
            applescript = self._lazy_import("applescript")

            # Kiểm tra khả năng truy cập điều khiển âm lượng
            result = applescript.run("get volume settings")
            if not result or result.code != 0:
                raise Exception("Không thể truy cập điều khiển âm lượng macOS")
            self.logger.debug("Khởi tạo điều khiển âm lượng macOS thành công")
        except Exception as e:
            self.logger.error(f"Khởi tạo điều khiển âm lượng macOS thất bại: {e}")
            raise

    def _init_linux(self) -> None:
        """
        Khởi tạo điều khiển âm lượng Linux.
        """
        # Kiểm tra công cụ theo thứ tự ưu tiên
        linux_tools = ["pactl", "wpctl", "amixer"]
        for tool in linux_tools:
            if shutil.which(tool):
                self.linux_tool = tool
                break

        # Kiểm tra alsamixer như phương án dự phòng
        if not self.linux_tool and shutil.which("alsamixer") and shutil.which("expect"):
            self.linux_tool = "alsamixer"

        if not self.linux_tool:
            self.logger.error("Không tìm thấy công cụ điều khiển âm lượng Linux phù hợp")
            raise Exception("Không tìm thấy công cụ điều khiển âm lượng Linux phù hợp")

        self.logger.debug(f"Khởi tạo điều khiển âm lượng Linux thành công, dùng: {self.linux_tool}")

    def get_volume(self) -> int:
        """
        Lấy âm lượng hiện tại (0-100)
        """
        get_method_name, _ = self.VOLUME_METHODS.get(self.system, (None, None))
        if not get_method_name:
            return self.DEFAULT_VOLUME

        get_method = getattr(self, get_method_name)
        return get_method()

    def set_volume(self, volume: int) -> None:
        """
        Đặt âm lượng (0-100)
        """
        # Đảm bảo âm lượng nằm trong khoảng hợp lệ
        volume = max(0, min(100, volume))

        _, set_method_name = self.VOLUME_METHODS.get(self.system, (None, None))
        if set_method_name:
            set_method = getattr(self, set_method_name)
            set_method(volume)

    @property
    def _get_windows_volume(self) -> Callable[[], int]:
        @self._safe_execute("Lấy âm lượng Windows", self.DEFAULT_VOLUME)
        def get_volume():
            if self._windows_com_thread_id != threading.get_ident():
                self._init_windows()
            volume_scalar = self.volume_control.GetMasterVolumeLevelScalar()
            return int(volume_scalar * 100)

        return get_volume

    @property
    def _set_windows_volume(self) -> Callable[[int], None]:
        @self._safe_execute("Đặt âm lượng Windows")
        def set_volume(volume):
            if self._windows_com_thread_id != threading.get_ident():
                self._init_windows()
            self.volume_control.SetMasterVolumeLevelScalar(volume / 100.0, None)

        return set_volume

    @property
    def _get_macos_volume(self) -> Callable[[], int]:
        @self._safe_execute("Lấy âm lượng macOS", self.DEFAULT_VOLUME)
        def get_volume():
            applescript = self._lazy_import("applescript")
            result = applescript.run("output volume of (get volume settings)")
            if result and result.out:
                return int(result.out.strip())
            return self.DEFAULT_VOLUME

        return get_volume

    @property
    def _set_macos_volume(self) -> Callable[[int], None]:
        @self._safe_execute("Đặt âm lượng macOS")
        def set_volume(volume):
            applescript = self._lazy_import("applescript")
            applescript.run(f"set volume output volume {volume}")

        return set_volume

    def _get_linux_volume(self) -> int:
        """
        Lấy âm lượng Linux.
        """
        get_method_name, _ = self.LINUX_VOLUME_METHODS.get(
            self.linux_tool, (None, None)
        )
        if not get_method_name:
            return self.DEFAULT_VOLUME

        get_method = getattr(self, get_method_name)
        return get_method()

    def _set_linux_volume(self, volume: int) -> None:
        """
        Đặt âm lượng Linux.
        """
        _, set_method_name = self.LINUX_VOLUME_METHODS.get(
            self.linux_tool, (None, None)
        )
        if set_method_name:
            set_method = getattr(self, set_method_name)
            set_method(volume)

    @property
    def _get_pactl_volume(self) -> Callable[[], int]:
        @self._safe_execute("Lấy âm lượng qua pactl", self.DEFAULT_VOLUME)
        def get_volume():
            result = self._run_command(["pactl", "list", "sinks"])
            if result and result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "Volume:" in line and "front-left:" in line:
                        match = re.search(r"(\d+)%", line)
                        if match:
                            return int(match.group(1))
            return self.DEFAULT_VOLUME

        return get_volume

    @property
    def _set_pactl_volume(self) -> Callable[[int], None]:
        @self._safe_execute("Đặt âm lượng qua pactl")
        def set_volume(volume):
            self._run_command(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"]
            )

        return set_volume

    @property
    def _get_wpctl_volume(self) -> Callable[[], int]:
        @self._safe_execute("Lấy âm lượng qua wpctl", self.DEFAULT_VOLUME)
        def get_volume():
            result = self._run_command(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], check=True
            )
            if result:
                return int(float(result.stdout.split(" ")[1]) * 100)
            return self.DEFAULT_VOLUME

        return get_volume

    @property
    def _set_wpctl_volume(self) -> Callable[[int], None]:
        @self._safe_execute("Đặt âm lượng qua wpctl")
        def set_volume(volume):
            self._run_command(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{volume}%"],
                check=True,
            )

        return set_volume

    @property
    def _get_amixer_volume(self) -> Callable[[], int]:
        @self._safe_execute("Lấy âm lượng qua amixer", self.DEFAULT_VOLUME)
        def get_volume():
            result = self._run_command(["amixer", "get", "Master"])
            if result and result.returncode == 0:
                match = re.search(r"(\d+)%", result.stdout)
                if match:
                    return int(match.group(1))
            return self.DEFAULT_VOLUME

        return get_volume

    @property
    def _set_amixer_volume(self) -> Callable[[int], None]:
        @self._safe_execute("Đặt âm lượng qua amixer")
        def set_volume(volume):
            self._run_command(["amixer", "sset", "Master", f"{volume}%"])

        return set_volume

    @property
    def _set_alsamixer_volume(self) -> Callable[[int], None]:
        @self._safe_execute("Đặt âm lượng qua alsamixer")
        def set_volume(volume):
            script = f"""
            spawn alsamixer
            send "m"
            send "{volume}"
            send "%"
            send "q"
            expect eof
            """
            self._run_command(["expect", "-c", script])

        return set_volume

    @staticmethod
    def check_dependencies() -> bool:
        """
        Kiểm tra và báo cáo các phụ thuộc bị thiếu.
        """
        system = platform.system()
        missing = []

        # Kiểm tra phụ thuộc Python module
        VolumeController._check_python_modules(system, missing)

        # Kiểm tra phụ thuộc công cụ Linux
        if system == "Linux":
            VolumeController._check_linux_tools(missing)

        # Báo cáo phụ thuộc bị thiếu
        return VolumeController._report_missing_dependencies(system, missing)

    @staticmethod
    def _check_python_modules(system: str, missing: List[str]) -> None:
        """
        Kiểm tra phụ thuộc Python module.
        """
        if system == "Windows":
            for module in ["pycaw", "comtypes"]:
                try:
                    __import__(module)
                except ImportError:
                    missing.append(module)
        elif system == "Darwin":  # macOS
            try:
                __import__("applescript")
            except ImportError:
                missing.append("applescript")

    @staticmethod
    def _check_linux_tools(missing: List[str]) -> None:
        """
        Kiểm tra phụ thuộc công cụ Linux.
        """
        tools = ["pactl", "wpctl", "amixer", "alsamixer"]
        found = any(shutil.which(tool) for tool in tools)
        if not found:
            missing.append("pulseaudio-utils, wireplumber hoặc alsa-utils")

    @staticmethod
    def _report_missing_dependencies(system: str, missing: List[str]) -> bool:
        """
        Báo cáo phụ thuộc bị thiếu.
        """
        if missing:
            print(f"Cảnh báo: tính năng điều khiển âm lượng cần các phụ thuộc sau nhưng chưa có: {', '.join(missing)}")
            print("Hãy dùng lệnh sau để cài các phụ thuộc còn thiếu:")
            if system in ["Windows", "Darwin"]:
                print("pip install " + " ".join(missing))
            elif system == "Linux":
                print("sudo apt-get install " + " ".join(missing))
            return False
        return True
