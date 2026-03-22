import asyncio
import importlib
import os
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import quote
from typing import Any

web = None
import requests

from src.mcp.tools.music import get_music_player_instance
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger
from src.utils.resource_finder import get_project_root
from src.utils.volume_controller import VolumeController

try:
    import pygame
except Exception:
    pygame = None

logger = get_logger(__name__)


class WebControlPlugin(Plugin):
    name = "web_control"

    def __init__(self) -> None:
        super().__init__()
        self.application: Any = None
        self._runner: Any = None
        self._site: Any = None
        self._last_volume_before_mute = 70
        self._last_effective_volume = 70
        self._host = os.environ.get("WEB_CONTROL_HOST", "0.0.0.0")
        self._port = int(os.environ.get("WEB_CONTROL_PORT", "8088"))
        default_html = Path(get_project_root()) / "assets" / "web" / "web_control.html"
        self._html_path = Path(
            os.environ.get("WEB_CONTROL_HTML_PATH", str(default_html))
        )
        default_yt_html = Path(get_project_root()) / "assets" / "web" / "youtube_music.html"
        self._yt_html_path = Path(
            os.environ.get("WEB_YOUTUBE_HTML_PATH", str(default_yt_html))
        )

    async def setup(self, app: Any) -> None:
        self.application = app

    async def start(self) -> None:
        global web
        if self._runner is not None:
            return
        if web is None:
            try:
                web = importlib.import_module("aiohttp.web")
            except Exception:
                logger.warning("aiohttp is not available, web control is disabled")
                return

        web_app = web.Application()
        web_app.add_routes(
            [
                web.get("/", self._handle_index),
                web.get("/youtube-music", self._handle_youtube_music_index),
                web.get("/api/status", self._handle_status),
                web.post("/api/ask", self._handle_ask),
                web.post("/api/listen/start", self._handle_listen_start),
                web.post("/api/listen/stop", self._handle_listen_stop),
                web.post("/api/music/play", self._handle_music_play),
                web.post("/api/music/toggle", self._handle_music_toggle),
                web.post("/api/music/stop", self._handle_music_stop),
                web.get("/api/youtube/recommendations", self._handle_youtube_recommendations),
                web.get("/api/youtube/next", self._handle_youtube_next),
                web.get("/api/youtube/stream", self._handle_youtube_stream),
                web.get("/api/youtube/status", self._handle_youtube_status),
                web.get("/api/volume", self._handle_get_volume),
                web.post("/api/volume", self._handle_set_volume),
                web.post("/api/volume/mute", self._handle_mute_volume),
                web.post("/api/volume/unmute", self._handle_unmute_volume),
                web.get("/api/logs", self._handle_logs),
                web.post("/api/restart", self._handle_restart),
            ]
        )

        self._runner = web.AppRunner(web_app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

        logger.info(
            "Web control started at http://%s:%s",
            self._host,
            self._port,
        )

    async def stop(self) -> None:
        try:
            if self._site is not None:
                await self._site.stop()
            if self._runner is not None:
                await self._runner.cleanup()
        finally:
            self._site = None
            self._runner = None

    async def _handle_index(self, request) -> Any:
        html = self._load_index_html()
        return web.Response(text=html, content_type="text/html")

    async def _handle_youtube_music_index(self, request) -> Any:
        html = self._load_youtube_html()
        return web.Response(text=html, content_type="text/html")

    async def _handle_status(self, request) -> Any:
        snapshot = self.application.get_state_snapshot()
        protocol_info = {}
        try:
            protocol = getattr(self.application, "protocol", None)
            if protocol and hasattr(protocol, "get_connection_info"):
                protocol_info = protocol.get_connection_info()
        except Exception:
            protocol_info = {}

        music_status = await get_music_player_instance().get_status()

        return web.json_response(
            {
                "ok": True,
                "app": snapshot,
                "protocol": protocol_info,
                "music": music_status,
                "web": {
                    "host": self._host,
                    "port": self._port,
                },
            }
        )

    async def _handle_ask(self, request) -> Any:
        payload = await self._read_json(request)
        text = str(payload.get("text", "")).strip()
        if not text:
            raise web.HTTPBadRequest(reason="text is required")

        local_result = await self._try_handle_local_command(text)
        if local_result is not None:
            status_code = 200 if local_result.get("ok", True) else 400
            return web.json_response(local_result, status=status_code)

        try:
            app = self.application
            if app is None:
                return web.json_response(
                    {"ok": False, "error": "Application is not ready"}, status=503
                )

            state_name = str(getattr(getattr(app, "device_state", None), "name", ""))
            if state_name == "SPEAKING":
                audio_plugin = app.plugins.get_plugin("audio") if app.plugins else None
                if audio_plugin:
                    try:
                        await audio_plugin.codec.clear_audio_queue()
                    except Exception:
                        pass
                await app.abort_speaking(None)

            ok = await app.connect_protocol()
            if not ok:
                return web.json_response(
                    {"ok": False, "error": "Protocol is not connected"}, status=503
                )

            if not getattr(app, "protocol", None):
                return web.json_response(
                    {"ok": False, "error": "Protocol is unavailable"}, status=503
                )

            app.set_chat_message("user", text)
            await app.protocol.send_wake_word_detected(text)
            return web.json_response(
                {"ok": True, "message": "Đã gửi câu hỏi", "text": text}
            )
        except Exception as e:
            logger.error("/api/ask failed: %s", e, exc_info=True)
            return web.json_response(
                {"ok": False, "error": f"ask failed: {str(e)}"}, status=500
            )

    async def _try_handle_local_command(self, text: str) -> dict | None:
        normalized = " ".join(str(text).strip().lower().split())
        if not normalized:
            return None

        # Mở nhạc qua lệnh chat web: "mở nhạc lạc trôi", "phát nhạc ..."
        music_patterns = [
            r"^\s*(?:mở|mo|phát|phat|bật|bat)\s+nhạc\s*(.*)$",
            r"^\s*(?:mở|mo|phát|phat|bật|bat)\s+bài\s*(.*)$",
        ]

        song_name = ""
        for pattern in music_patterns:
            match = re.match(pattern, text, flags=re.IGNORECASE)
            if match:
                song_name = (match.group(1) or "").strip()
                break

        if song_name or any(normalized.startswith(p) for p in ("mở nhạc", "mo nhac", "phát nhạc", "phat nhac", "bật nhạc", "bat nhac", "mở bài", "mo bai", "phát bài", "phat bai", "bật bài", "bat bai")):
            if not song_name:
                return {
                    "ok": False,
                    "message": "Bạn cần nói rõ tên bài, ví dụ: mở nhạc Lạc Trôi",
                    "source": "local-command",
                }
            result = await get_music_player_instance().search_and_play(song_name)
            return {
                "ok": result.get("status") == "success",
                "source": "local-command",
                **result,
            }

        # Dừng nhạc
        if any(k in normalized for k in ("dừng nhạc", "dung nhac", "tắt nhạc", "tat nhac", "stop nhạc", "stop nhac")):
            result = await get_music_player_instance().stop()
            return {
                "ok": result.get("status") in {"success", "info"},
                "source": "local-command",
                **result,
            }

        # Toggle/tạm dừng/tiếp tục nhạc
        if any(k in normalized for k in ("tạm dừng nhạc", "tam dung nhac", "tiếp tục nhạc", "tiep tuc nhac", "pause nhạc", "pause nhac", "play nhạc", "play nhac")):
            result = await get_music_player_instance().play_pause()
            return {
                "ok": result.get("status") in {"success", "info"},
                "source": "local-command",
                **result,
            }

        return None

    async def _handle_music_play(self, request) -> Any:
        payload = await self._read_json(request)
        song_name = str(payload.get("song_name", "")).strip()
        if not song_name:
            raise web.HTTPBadRequest(reason="song_name is required")

        result = await get_music_player_instance().search_and_play(song_name)
        status_code = 200 if result.get("status") == "success" else 400
        return web.json_response({"ok": status_code == 200, **result}, status=status_code)

    async def _handle_listen_start(self, request) -> Any:
        try:
            app = self.application
            if app is None:
                return web.json_response(
                    {"ok": False, "error": "Application is not ready"}, status=503
                )

            await app.start_listening_manual()
            return web.json_response({"ok": True, "message": "Đã bắt đầu lắng nghe"})
        except Exception as e:
            logger.error("/api/listen/start failed: %s", e, exc_info=True)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_listen_stop(self, request) -> Any:
        try:
            app = self.application
            if app is None:
                return web.json_response(
                    {"ok": False, "error": "Application is not ready"}, status=503
                )

            await app.stop_listening_manual()
            return web.json_response({"ok": True, "message": "Đã dừng lắng nghe"})
        except Exception as e:
            logger.error("/api/listen/stop failed: %s", e, exc_info=True)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_music_toggle(self, request) -> Any:
        result = await get_music_player_instance().play_pause()
        status_code = 200 if result.get("status") in {"success", "info"} else 400
        return web.json_response({"ok": status_code == 200, **result}, status=status_code)

    async def _handle_music_stop(self, request) -> Any:
        result = await get_music_player_instance().stop()
        status_code = 200 if result.get("status") in {"success", "info"} else 400
        return web.json_response({"ok": status_code == 200, **result}, status=status_code)

    def _get_youtube_server_base(self) -> str:
        return os.environ.get("YT_MUSIC_SERVER_URL", "https://youtube-proxy.imsteve.dev").rstrip("/")

    async def _handle_youtube_status(self, request) -> Any:
        base = self._get_youtube_server_base()
        return web.json_response({"ok": True, "server": base})

    async def _handle_youtube_recommendations(self, request) -> Any:
        query = str(request.query.get("q", "")).strip()
        if not query:
            raise web.HTTPBadRequest(reason="q is required")

        history = str(request.query.get("history", "")).strip()
        limit = str(request.query.get("limit", "10")).strip()
        base = self._get_youtube_server_base()

        try:
            resp = await asyncio.to_thread(
                requests.get,
                f"{base}/api/recommendations",
                params={"q": query, "history": history, "limit": limit},
                timeout=15,
            )
            payload = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                return web.json_response(
                    {"ok": False, "error": payload.get("error") if isinstance(payload, dict) else "request failed"},
                    status=resp.status_code,
                )
            return web.json_response({"ok": True, **(payload if isinstance(payload, dict) else {})})
        except Exception as e:
            logger.error("/api/youtube/recommendations failed: %s", e, exc_info=True)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_youtube_next(self, request) -> Any:
        query = str(request.query.get("q", "")).strip()
        if not query:
            raise web.HTTPBadRequest(reason="q is required")

        history = str(request.query.get("history", "")).strip()
        base = self._get_youtube_server_base()

        try:
            resp = await asyncio.to_thread(
                requests.get,
                f"{base}/api/next",
                params={"q": query, "history": history},
                timeout=15,
            )
            payload = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                return web.json_response(
                    {"ok": False, "error": payload.get("error") if isinstance(payload, dict) else "request failed"},
                    status=resp.status_code,
                )
            return web.json_response({"ok": True, **(payload if isinstance(payload, dict) else {})})
        except Exception as e:
            logger.error("/api/youtube/next failed: %s", e, exc_info=True)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_youtube_stream(self, request) -> Any:
        query = str(request.query.get("q", "")).strip()
        if not query:
            raise web.HTTPBadRequest(reason="q is required")

        base = self._get_youtube_server_base()
        stream_url = f"{base}/stream?q={quote(query)}"
        raise web.HTTPFound(location=stream_url)

    async def _handle_get_volume(self, request) -> Any:
        try:
            system_volume = None
            if VolumeController.check_dependencies():
                controller = await asyncio.to_thread(VolumeController)
                system_volume = await asyncio.to_thread(controller.get_volume)

            music_volume = self._get_music_volume()

            effective_volume = None
            if music_volume is not None:
                effective_volume = music_volume
            elif system_volume is not None:
                effective_volume = int(system_volume)
            else:
                effective_volume = self._last_effective_volume

            effective_volume = max(0, min(100, int(effective_volume)))
            self._last_effective_volume = effective_volume
            return web.json_response(
                {
                    "ok": True,
                    "volume": effective_volume,
                    "system_volume": (
                        None if system_volume is None else max(0, min(100, int(system_volume)))
                    ),
                    "music_volume": music_volume,
                }
            )
        except Exception as e:
            logger.error("/api/volume GET failed: %s", e, exc_info=True)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_set_volume(self, request) -> Any:
        payload = await self._read_json(request)
        if "volume" not in payload:
            raise web.HTTPBadRequest(reason="volume is required")

        try:
            volume = int(payload.get("volume"))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(reason="volume must be an integer")

        volume = max(0, min(100, volume))

        try:
            system_ok = False
            system_volume = None

            if VolumeController.check_dependencies():
                controller = await asyncio.to_thread(VolumeController)
                await asyncio.to_thread(controller.set_volume, volume)
                system_volume = await asyncio.to_thread(controller.get_volume)
                system_volume = max(0, min(100, int(system_volume)))
                system_ok = True

            music_ok = self._set_music_volume(volume)
            music_volume = self._get_music_volume()

            if music_volume is not None:
                effective_volume = music_volume
            elif system_volume is not None:
                effective_volume = system_volume
            else:
                effective_volume = volume

            effective_volume = max(0, min(100, int(effective_volume)))
            self._last_effective_volume = effective_volume

            if effective_volume > 0:
                self._last_volume_before_mute = effective_volume

            if not system_ok and not music_ok:
                return web.json_response(
                    {
                        "ok": False,
                        "error": "Không thể điều khiển âm lượng hệ thống hoặc nhạc",
                        "volume": effective_volume,
                    },
                    status=503,
                )

            return web.json_response(
                {
                    "ok": True,
                    "message": f"Đã đặt âm lượng: {effective_volume}%",
                    "volume": effective_volume,
                    "system_ok": system_ok,
                    "music_ok": music_ok,
                    "system_volume": system_volume,
                    "music_volume": music_volume,
                }
            )
        except Exception as e:
            logger.error("/api/volume POST failed: %s", e, exc_info=True)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_mute_volume(self, request) -> Any:
        try:
            current_volume = self._last_effective_volume
            system_ok = False

            if VolumeController.check_dependencies():
                controller = await asyncio.to_thread(VolumeController)
                current_volume = await asyncio.to_thread(controller.get_volume)
                current_volume = max(0, min(100, int(current_volume)))
                await asyncio.to_thread(controller.set_volume, 0)
                system_ok = True

            music_ok = self._set_music_volume(0)

            if current_volume > 0:
                self._last_volume_before_mute = current_volume
            self._last_effective_volume = 0

            return web.json_response(
                {
                    "ok": bool(system_ok or music_ok),
                    "message": "Đã tắt tiếng",
                    "volume": 0,
                    "previous_volume": self._last_volume_before_mute,
                    "system_ok": system_ok,
                    "music_ok": music_ok,
                }
            )
        except Exception as e:
            logger.error("/api/volume/mute failed: %s", e, exc_info=True)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    async def _handle_unmute_volume(self, request) -> Any:
        try:
            target = max(1, min(100, int(self._last_volume_before_mute or 70)))
            system_ok = False
            current_volume = target

            if VolumeController.check_dependencies():
                controller = await asyncio.to_thread(VolumeController)
                await asyncio.to_thread(controller.set_volume, target)
                current_volume = await asyncio.to_thread(controller.get_volume)
                current_volume = max(0, min(100, int(current_volume)))
                system_ok = True

            music_ok = self._set_music_volume(target)
            music_volume = self._get_music_volume()
            if music_volume is not None:
                current_volume = music_volume

            self._last_effective_volume = current_volume

            return web.json_response(
                {
                    "ok": bool(system_ok or music_ok),
                    "message": f"Đã bật tiếng: {current_volume}%",
                    "volume": current_volume,
                    "system_ok": system_ok,
                    "music_ok": music_ok,
                }
            )
        except Exception as e:
            logger.error("/api/volume/unmute failed: %s", e, exc_info=True)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    def _set_music_volume(self, volume: int) -> bool:
        try:
            if pygame is None:
                return False
            if not pygame.mixer.get_init():
                return False
            volume = max(0, min(100, int(volume)))
            pygame.mixer.music.set_volume(volume / 100.0)
            return True
        except Exception:
            return False

    def _get_music_volume(self) -> int | None:
        try:
            if pygame is None:
                return None
            if not pygame.mixer.get_init():
                return None
            value = pygame.mixer.music.get_volume()
            if value is None:
                return None
            return max(0, min(100, int(round(float(value) * 100))))
        except Exception:
            return None

    async def _handle_logs(self, request) -> Any:
        lines_param = request.query.get("lines", "200")
        try:
            lines = max(10, min(int(lines_param), 2000))
        except Exception:
            lines = 200

        log_lines = self._read_last_log_lines(lines)
        return web.json_response({"ok": True, "lines": log_lines, "count": len(log_lines)})

    async def _handle_restart(self, request) -> Any:
        self.application.spawn(self._restart_process(), "web:restart")
        return web.json_response({"ok": True, "message": "Đang restart ứng dụng..."})

    async def _restart_process(self) -> None:
        await asyncio.sleep(0.5)
        python = sys.executable
        script = sys.argv[0]
        args = sys.argv[1:]

        logger.warning("Web requested restart")
        if getattr(sys, "frozen", False):
            os.execv(sys.executable, [sys.executable] + args)
        else:
            os.execv(python, [python, script] + args)

    async def _read_json(self, request) -> dict:
        try:
            data = await request.json()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _read_last_log_lines(self, lines: int) -> list[str]:
        project_root = get_project_root()
        log_file = Path(project_root) / "logs" / "app.log"
        if not log_file.exists():
            return ["Log file not found: logs/app.log"]

        queue: deque[str] = deque(maxlen=lines)
        try:
            with log_file.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    queue.append(line.rstrip("\n"))
        except Exception as e:
            return [f"Failed to read log file: {e}"]

        return list(queue)

    def _load_index_html(self) -> str:
        try:
            if self._html_path.exists():
                return self._html_path.read_text(encoding="utf-8")
            logger.warning("Web HTML file not found: %s", self._html_path)
        except Exception as e:
            logger.error("Failed to load web html: %s", e)

        return (
            "<html><body><h1>Web UI not found</h1><p>Set WEB_CONTROL_HTML_PATH "
            "or create assets/web/web_control.html</p></body></html>"
        )

    def _load_youtube_html(self) -> str:
        try:
            if self._yt_html_path.exists():
                return self._yt_html_path.read_text(encoding="utf-8")
            logger.warning("YouTube Web HTML file not found: %s", self._yt_html_path)
        except Exception as e:
            logger.error("Failed to load youtube web html: %s", e)

        return (
            "<html><body><h1>YouTube Music UI not found</h1><p>Set WEB_YOUTUBE_HTML_PATH "
            "or create assets/web/youtube_music.html</p></body></html>"
        )
