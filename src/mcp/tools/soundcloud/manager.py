"""MCP tools for SoundCloud playback."""

import asyncio
import os
from typing import Any, Dict
from urllib.parse import quote

import requests

from src.mcp.tools.music import get_music_player_instance
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


def _base() -> str:
    return os.environ.get(
        "SC_MUSIC_SERVER_URL", "https://youtube-proxy.imsteve.dev"
    ).rstrip("/")


class SoundCloudToolsManager:
    """Expose SoundCloud search/playback through the existing music player."""

    def __init__(self) -> None:
        self._initialized = False
        self._player = None

    def init_tools(self, add_tool, PropertyList, Property, PropertyType) -> None:
        self._player = get_music_player_instance()

        async def play(args: Dict[str, Any]) -> str:
            query = str(args.get("query", "")).strip()
            if not query:
                return "Vui lòng cung cấp tên bài hát hoặc SoundCloud URL."
            base = _base()
            title = query
            track_url = query

            # Nếu không phải URL SoundCloud, tìm kiếm trước
            if "soundcloud.com/" not in query:
                resp = await asyncio.to_thread(
                    requests.get,
                    f"{base}/soundcloud/search",
                    params={"q": query, "limit": 1},
                    timeout=15,
                )
                resp.raise_for_status()
                tracks = resp.json().get("tracks", [])
                if not tracks:
                    return f"Không tìm thấy bài hát SoundCloud: {query}"
                first = tracks[0]
                track_url = str(first.get("url") or first.get("permalink_url") or "").strip()
                title = str(first.get("title") or query).strip()

            bitrate = str(args.get("bitrate", "128k"))
            stream_url = f"{base}/soundcloud/stream/mp3?url={quote(track_url)}&bitrate={quote(bitrate)}"
            result = await self._player.play_stream_url(stream_url, title=title)
            return result.get("message", "Đã phát SoundCloud.")

        async def toggle(args: Dict[str, Any]) -> str:
            result = await self._player.play_pause()
            return result.get("message", "Đã chuyển trạng thái phát.")

        async def stop(args: Dict[str, Any]) -> str:
            result = await self._player.stop()
            return result.get("message", "Đã dừng phát.")

        async def status(args: Dict[str, Any]) -> str:
            result = await self._player.get_status()
            return (
                f"Bài hát: {result.get('current_song', 'Không có')}\n"
                f"Player: {result.get('player_backend', 'Không có')}\n"
                f"Trạng thái: {'đang phát' if result.get('is_playing') else 'đã dừng'}"
            )

        async def search(args: Dict[str, Any]) -> str:
            query = str(args.get("query", "")).strip()
            limit = int(args.get("limit", 5))
            if not query:
                return "Vui lòng cung cấp từ khóa tìm kiếm."
            resp = await asyncio.to_thread(
                requests.get,
                f"{_base()}/soundcloud/search",
                params={"q": query, "limit": limit},
                timeout=15,
            )
            resp.raise_for_status()
            tracks = resp.json().get("tracks", [])
            if not tracks:
                return f"Không tìm thấy kết quả cho: {query}"
            lines = [f"{i+1}. {t.get('title', '?')} — {t.get('url', '')}" for i, t in enumerate(tracks)]
            return "\n".join(lines)

        add_tool((
            "soundcloud.play",
            "Tìm kiếm và phát bài hát từ SoundCloud qua loa thiết bị. "
            "Nhận tên bài/nghệ sĩ hoặc SoundCloud URL trực tiếp. "
            "Ví dụ: 'Lo-fi hip hop', 'Alan Walker Faded', 'https://soundcloud.com/...'",
            PropertyList([
                Property("query", PropertyType.STRING),
                Property("bitrate", PropertyType.STRING),
            ]),
            play,
        ))
        add_tool((
            "soundcloud.play_pause",
            "Tạm dừng hoặc tiếp tục phát SoundCloud.",
            PropertyList(),
            toggle,
        ))
        add_tool((
            "soundcloud.stop",
            "Dừng phát SoundCloud.",
            PropertyList(),
            stop,
        ))
        add_tool((
            "soundcloud.get_status",
            "Lấy trạng thái phát SoundCloud hiện tại.",
            PropertyList(),
            status,
        ))
        add_tool((
            "soundcloud.search",
            "Tìm kiếm bài hát trên SoundCloud và trả về danh sách kết quả (không phát).",
            PropertyList([
                Property("query", PropertyType.STRING),
                Property("limit", PropertyType.INTEGER),
            ]),
            search,
        ))

        self._initialized = True
        logger.info("[SoundCloudManager] Registered SoundCloud MCP tools")

    def get_status(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "tools_count": 5,
            "available_tools": ["play", "play_pause", "stop", "get_status", "search"],
        }


_soundcloud_tools_manager: SoundCloudToolsManager | None = None


def get_soundcloud_tools_manager() -> SoundCloudToolsManager:
    global _soundcloud_tools_manager
    if _soundcloud_tools_manager is None:
        _soundcloud_tools_manager = SoundCloudToolsManager()
    return _soundcloud_tools_manager
