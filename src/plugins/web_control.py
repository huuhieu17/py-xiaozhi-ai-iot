import asyncio
import importlib
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any

web = None

from src.mcp.tools.music import get_music_player_instance
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger
from src.utils.resource_finder import get_project_root

logger = get_logger(__name__)


class WebControlPlugin(Plugin):
    name = "web_control"

    def __init__(self) -> None:
        super().__init__()
        self.application: Any = None
        self._runner: Any = None
        self._site: Any = None
        self._host = os.environ.get("WEB_CONTROL_HOST", "0.0.0.0")
        self._port = int(os.environ.get("WEB_CONTROL_PORT", "8088"))

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
                web.get("/api/status", self._handle_status),
                web.post("/api/ask", self._handle_ask),
                web.post("/api/music/play", self._handle_music_play),
                web.post("/api/music/toggle", self._handle_music_toggle),
                web.post("/api/music/stop", self._handle_music_stop),
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
        html = f"""
<!doctype html>
<html lang=\"vi\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Xiaozhi Web Control</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; max-width: 820px; }}
    h2 {{ margin-top: 24px; }}
    .row {{ display: flex; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }}
    input[type=text], input[type=number] {{ flex: 1; padding: 8px; min-width: 260px; }}
    button {{ padding: 8px 14px; cursor: pointer; }}
    pre {{ background: #111; color: #ddd; padding: 12px; border-radius: 6px; overflow: auto; max-height: 360px; }}
    #status {{ background: #f6f6f6; padding: 10px; border-radius: 6px; }}
  </style>
</head>
<body>
  <h1>Xiaozhi Web Control</h1>
  <div id=\"status\">Đang tải trạng thái...</div>

  <h2>Hỏi AI</h2>
  <div class=\"row\">
    <input id=\"askText\" type=\"text\" placeholder=\"Nhập câu hỏi...\" />
    <button onclick=\"askAI()\">Gửi</button>
  </div>

  <h2>Mở nhạc</h2>
  <div class=\"row\">
    <input id=\"songName\" type=\"text\" placeholder=\"Tên bài hát\" />
    <button onclick=\"playMusic()\">Mở nhạc</button>
    <button onclick=\"toggleMusic()\">Play/Pause</button>
    <button onclick=\"stopMusic()\">Stop</button>
  </div>

  <h2>Logs</h2>
  <div class=\"row\">
    <input id=\"logLines\" type=\"number\" min=\"10\" max=\"2000\" value=\"200\" />
    <button onclick=\"loadLogs()\">Tải log</button>
  </div>
  <pre id=\"logs\">Chưa tải logs...</pre>

  <h2>Restart</h2>
  <div class=\"row\">
    <button onclick=\"restartApp()\">Restart ứng dụng</button>
  </div>

<script>
async function callApi(url, method='GET', body=null) {{
  const options = {{ method, headers: {{ 'Content-Type': 'application/json' }} }};
  if (body) options.body = JSON.stringify(body);
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({{ ok: false, error: 'Invalid response' }}));
  if (!res.ok) throw new Error(data.error || res.statusText || 'Request failed');
  return data;
}}

async function refreshStatus() {{
  try {{
    const data = await callApi('/api/status');
    document.getElementById('status').innerText = JSON.stringify(data, null, 2);
  }} catch (e) {{
    document.getElementById('status').innerText = 'Lỗi status: ' + e.message;
  }}
}}

async function askAI() {{
  const text = document.getElementById('askText').value.trim();
  if (!text) return;
  try {{
    const data = await callApi('/api/ask', 'POST', {{ text }});
    alert(data.message || 'Đã gửi câu hỏi');
  }} catch (e) {{
    alert('Lỗi hỏi AI: ' + e.message);
  }}
}}

async function playMusic() {{
  const song_name = document.getElementById('songName').value.trim();
  if (!song_name) return;
  try {{
    const data = await callApi('/api/music/play', 'POST', {{ song_name }});
    alert(data.message || 'Đã xử lý mở nhạc');
    refreshStatus();
  }} catch (e) {{
    alert('Lỗi mở nhạc: ' + e.message);
  }}
}}

async function toggleMusic() {{
  try {{
    const data = await callApi('/api/music/toggle', 'POST');
    alert(data.message || 'Đã toggle nhạc');
    refreshStatus();
  }} catch (e) {{
    alert('Lỗi toggle nhạc: ' + e.message);
  }}
}}

async function stopMusic() {{
  try {{
    const data = await callApi('/api/music/stop', 'POST');
    alert(data.message || 'Đã dừng nhạc');
    refreshStatus();
  }} catch (e) {{
    alert('Lỗi dừng nhạc: ' + e.message);
  }}
}}

async function loadLogs() {{
  const lines = Number(document.getElementById('logLines').value || 200);
  try {{
    const data = await callApi('/api/logs?lines=' + encodeURIComponent(lines));
    document.getElementById('logs').innerText = (data.lines || []).join('\n');
  }} catch (e) {{
    document.getElementById('logs').innerText = 'Lỗi tải logs: ' + e.message;
  }}
}}

async function restartApp() {{
  if (!confirm('Bạn chắc chắn muốn restart ứng dụng?')) return;
  try {{
    const data = await callApi('/api/restart', 'POST');
    alert(data.message || 'Đang restart');
  }} catch (e) {{
    alert('Lỗi restart: ' + e.message);
  }}
}}

refreshStatus();
loadLogs();
setInterval(refreshStatus, 4000);
</script>
</body>
</html>
"""
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

        if self.application.device_state.name == "SPEAKING":
            audio_plugin = self.application.plugins.get_plugin("audio")
            if audio_plugin:
                try:
                    await audio_plugin.codec.clear_audio_queue()
                except Exception:
                    pass
            await self.application.abort_speaking(None)

        ok = await self.application.connect_protocol()
        if not ok:
            return web.json_response(
                {"ok": False, "error": "Protocol is not connected"}, status=503
            )

        self.application.set_chat_message("user", text)
        await self.application.protocol.send_wake_word_detected(text)
        return web.json_response(
            {"ok": True, "message": "Đã gửi câu hỏi", "text": text}
        )

    async def _handle_music_play(self, request) -> Any:
        payload = await self._read_json(request)
        song_name = str(payload.get("song_name", "")).strip()
        if not song_name:
            raise web.HTTPBadRequest(reason="song_name is required")

        result = await get_music_player_instance().search_and_play(song_name)
        status_code = 200 if result.get("status") == "success" else 400
        return web.json_response({"ok": status_code == 200, **result}, status=status_code)

    async def _handle_music_toggle(self, request) -> Any:
        result = await get_music_player_instance().play_pause()
        status_code = 200 if result.get("status") in {"success", "info"} else 400
        return web.json_response({"ok": status_code == 200, **result}, status=status_code)

    async def _handle_music_stop(self, request) -> Any:
        result = await get_music_player_instance().stop()
        status_code = 200 if result.get("status") in {"success", "info"} else 400
        return web.json_response({"ok": status_code == 200, **result}, status=status_code)

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
