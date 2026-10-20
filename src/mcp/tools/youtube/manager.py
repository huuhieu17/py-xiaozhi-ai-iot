"""MCP tools for YouTube Music playback."""

import asyncio
import os
from urllib.parse import quote
from typing import Any, Dict

import requests

from src.mcp.tools.music import get_music_player_instance
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class YouTubeToolsManager:
    """Expose YouTube search/playback through the existing web/player service."""

    def __init__(self) -> None:
        self._initialized = False
        self._player = None

    def init_tools(self, add_tool, PropertyList, Property, PropertyType) -> None:
        self._player = get_music_player_instance()

        async def play(args: Dict[str, Any]) -> str:
            query = str(args.get("query", "")).strip()
            if not query:
                return "Vui lòng cung cấp tên bài hát hoặc YouTube URL."
            base = os.environ.get(
                "YT_MUSIC_SERVER_URL", "https://youtube-proxy.imsteve.dev"
            ).rstrip("/")
            video_id = query
            title = query
            if not ("youtube.com/" in query or "youtu.be/" in query):
                search = await asyncio.to_thread(
                    requests.get,
                    f"{base}/youtube/search",
                    params={"q": query, "limit": 1},
                    timeout=15,
                )
                search.raise_for_status()
                songs = search.json().get("songs", [])
                if not songs:
                    return f"Không tìm thấy bài hát YouTube: {query}"
                first = songs[0]
                video_id = str(first.get("id") or first.get("videoId") or "").strip()
                title = str(first.get("title") or query).strip()
            else:
                if "youtu.be/" in query:
                    video_id = query.rsplit("/", 1)[-1].split("?", 1)[0]
                elif "v=" in query:
                    video_id = query.split("v=", 1)[1].split("&", 1)[0]
            stream_url = (
                f"{base}/youtube/play?videoId={quote(video_id)}&bitrate=192k"
            )
            result = await self._player.play_stream_url(stream_url, title=title)
            return result.get("message", "Đã xử lý yêu cầu phát YouTube.")

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

        add_tool((
            "youtube_music.play",
            "Play a YouTube audio URL through the device audio player.",
            PropertyList([Property("query", PropertyType.STRING)]),
            play,
        ))
        add_tool((
            "youtube_music.play_pause",
            "Pause or resume the current YouTube Music playback.",
            PropertyList(),
            toggle,
        ))
        add_tool((
            "youtube_music.stop",
            "Stop YouTube Music playback.",
            PropertyList(),
            stop,
        ))
        add_tool((
            "youtube_music.get_status",
            "Get the current YouTube Music playback and player backend status.",
            PropertyList(),
            status,
        ))
        self._initialized = True
        logger.info("[YouTubeManager] Registered YouTube Music MCP tools")

    def get_status(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "tools_count": 4,
            "available_tools": [
                "play",
                "play_pause",
                "stop",
                "get_status",
            ],
        }


_youtube_tools_manager = None


def get_youtube_tools_manager() -> YouTubeToolsManager:
    global _youtube_tools_manager
    if _youtube_tools_manager is None:
        _youtube_tools_manager = YouTubeToolsManager()
    return _youtube_tools_manager
