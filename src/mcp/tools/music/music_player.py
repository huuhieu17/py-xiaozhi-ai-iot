"""Triển khai singleton trình phát nhạc.

Cung cấp trình phát nhạc kiểu singleton, khởi tạo khi đăng ký, hỗ trợ hoạt động bất đồng bộ.
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from src.utils.logging_config import get_logger
from src.utils.resource_finder import get_user_cache_dir

# Cố gắng nhập thư viện metadata âm nhạc
try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3NoHeaderError

    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

logger = get_logger(__name__)


class MusicMetadata:
    """
    Lớp metadata âm nhạc.
    """

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.filename = file_path.name
        self.file_id = file_path.stem  # Tên tệp không có phần mở rộng, tức là ID bài hát
        self.file_size = file_path.stat().st_size

        # Metadata trích xuất từ tệp
        self.title = None
        self.artist = None
        self.album = None
        self.duration = None  # Số giây

    def extract_metadata(self) -> bool:
        """
        Trích xuất metadata tệp nhạc.
        """
        if not MUTAGEN_AVAILABLE:
            return False

        try:
            audio_file = MutagenFile(self.file_path)
            if audio_file is None:
                return False

            # Thông tin cơ bản
            if hasattr(audio_file, "info"):
                self.duration = getattr(audio_file.info, "length", None)

            # Thông tin thẻ ID3
            tags = audio_file.tags if audio_file.tags else {}

            # Tiêu đề
            self.title = self._get_tag_value(tags, ["TIT2", "TITLE", "\xa9nam"])

            # Nghệ sĩ
            self.artist = self._get_tag_value(tags, ["TPE1", "ARTIST", "\xa9ART"])

            # Album
            self.album = self._get_tag_value(tags, ["TALB", "ALBUM", "\xa9alb"])

            return True

        except ID3NoHeaderError:
            # Không có thẻ ID3, không phải lỗi
            return True
        except Exception as e:
            logger.debug(f"Trích xuất metadata thất bại {self.filename}: {e}")
            return False

    def _get_tag_value(self, tags: dict, tag_names: List[str]) -> Optional[str]:
        """
        Lấy giá trị từ nhiều tên thẻ có thể.
        """
        for tag_name in tag_names:
            if tag_name in tags:
                value = tags[tag_name]
                if isinstance(value, list) and value:
                    return str(value[0])
                elif value:
                    return str(value)
        return None

    def format_duration(self) -> str:
        """
        Định dạng thời lượng phát.
        """
        if self.duration is None:
            return "Không xác định"

        minutes = int(self.duration) // 60
        seconds = int(self.duration) % 60
        return f"{minutes:02d}:{seconds:02d}"


class MusicPlayer:
    """Trình phát nhạc - Thiết kế dành riêng cho thiết bị IoT

    Chỉ giữ lại các chức năng cốt lõi: tìm kiếm, phát, tạm dừng, dừng, tua
    """

    def __init__(self):
        # Trạng thái phát cốt lõi
        self.current_song = ""
        self.current_url = ""
        self._playback_target = ""
        self.song_id = ""
        self.total_duration = 0
        self.is_playing = False
        self.paused = False
        self.current_position = 0
        self.start_play_time = 0
        self._volume_percent = 70

        # Liên quan đến lời bài hát
        self.lyrics = []  # Danh sách lời bài hát, định dạng [(thời gian, văn bản), ...]
        self.current_lyric_index = -1  # Chỉ mục lời bài hát hiện tại

        # Cài đặt thư mục cache - Sử dụng thư mục cache người dùng để đảm bảo quyền ghi
        user_cache_dir = get_user_cache_dir()
        self.cache_dir = user_cache_dir / "music"
        self.temp_cache_dir = self.cache_dir / "temp"
        self._init_cache_dirs()

        # Cấu hình API
        self.config = {
            "SEARCH_URL": "https://music-proxy.imsteve.dev",
            "PLAY_URL": "https://music-proxy.imsteve.dev",
            "LYRIC_URL": "https://music-proxy.imsteve.dev",
            "HEADERS": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " "AppleWebKit/537.36"
                ),
                "Accept": "*/*",
                "Connection": "keep-alive",
            },
        }

        # Dọn dẹp cache tạm thời
        self._clean_temp_cache()

        # Lấy instance ứng dụng
        self.app = None
        self._initialize_app_reference()

        # Cache danh sách nhạc cục bộ
        self._local_playlist = None
        self._last_scan_time = 0

        # Trạng thái phát stream URL bằng player ngoài (ffplay/mpv/vlc)
        self._stream_player_process: Optional[subprocess.Popen] = None
        self._stream_player_backend: str = ""
        self._stream_monitor_task: Optional[asyncio.Task] = None
        self._is_stream_url = False

        # MPV IPC socket support cho volume realtime
        self._mpv_ipc_socket_path: Optional[str] = None
        self._mpv_command_id = 0

        logger.info("Khởi tạo singleton trình phát nhạc hoàn tất")

    def _initialize_app_reference(self):
        """
        Khởi tạo tham chiếu ứng dụng.
        """
        try:
            from src.application import Application

            self.app = Application.get_instance()
        except Exception as e:
            logger.warning(f"Lấy instance Application thất bại: {e}")
            self.app = None

    def _init_cache_dirs(self):
        """
        Khởi tạo thư mục cache.
        """
        try:
            # Tạo thư mục cache chính
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            # Tạo thư mục cache tạm thời
            self.temp_cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Khởi tạo thư mục cache nhạc hoàn tất: {self.cache_dir}")
        except Exception as e:
            logger.error(f"Tạo thư mục cache thất bại: {e}")
            # Quay lại thư mục tạm thời hệ thống
            self.cache_dir = Path(tempfile.gettempdir()) / "xiaozhi_music_cache"
            self.temp_cache_dir = self.cache_dir / "temp"
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.temp_cache_dir.mkdir(parents=True, exist_ok=True)

    def _clean_temp_cache(self):
        """
        Dọn dẹp tệp cache tạm thời.
        """
        try:
            # Xóa tất cả các tệp trong thư mục cache tạm thời
            for file_path in self.temp_cache_dir.glob("*"):
                try:
                    if file_path.is_file():
                        file_path.unlink()
                        logger.debug(f"Đã xóa tệp cache tạm thời: {file_path.name}")
                except Exception as e:
                    logger.warning(f"Xóa tệp cache tạm thời thất bại: {file_path.name}, {e}")

            logger.info("Dọn dẹp cache nhạc tạm thời hoàn tất")
        except Exception as e:
            logger.error(f"Dọn dẹp thư mục cache tạm thời thất bại: {e}")

    def _scan_local_music(self, force_refresh: bool = False) -> List[MusicMetadata]:
        """
        Quét cache nhạc cục bộ, trả về danh sách bài hát.
        """
        current_time = time.time()

        # Nếu không bắt buộc làm mới và cache chưa hết hạn (5 phút), trả về cache trực tiếp
        if (
            not force_refresh
            and self._local_playlist is not None
            and (current_time - self._last_scan_time) < 300
        ):
            return self._local_playlist

        playlist = []

        if not self.cache_dir.exists():
            logger.warning(f"Thư mục cache không tồn tại: {self.cache_dir}")
            return playlist

        # Tìm tất cả các tệp nhạc
        music_files = []
        for pattern in ["*.mp3", "*.m4a", "*.flac", "*.wav", "*.ogg"]:
            music_files.extend(self.cache_dir.glob(pattern))

        logger.debug(f"Tìm thấy {len(music_files)} tệp nhạc")

        # Quét từng tệp
        for file_path in music_files:
            try:
                metadata = MusicMetadata(file_path)

                # Cố gắng trích xuất metadata
                if MUTAGEN_AVAILABLE:
                    metadata.extract_metadata()

                playlist.append(metadata)

            except Exception as e:
                logger.debug(f"Xử lý tệp nhạc thất bại {file_path.name}: {e}")

        # Sắp xếp theo nghệ sĩ và tiêu đề
        playlist.sort(key=lambda x: (x.artist or "Unknown", x.title or x.filename))

        # Cập nhật cache
        self._local_playlist = playlist
        self._last_scan_time = current_time

        logger.info(f"Quét hoàn tất, tìm thấy {len(playlist)} bài nhạc cục bộ")
        return playlist

    async def get_local_playlist(self, force_refresh: bool = False) -> dict:
        """
        Lấy danh sách nhạc cục bộ.
        """
        try:
            playlist = self._scan_local_music(force_refresh)

            if not playlist:
                return {
                    "status": "info",
                    "message": "Không có tệp nhạc trong bộ nhớ đệm cục bộ",
                    "playlist": [],
                    "total_count": 0,
                }

            # Định dạng danh sách phát, định dạng ngắn gọn thuận tiện cho AI đọc
            formatted_playlist = []
            for metadata in playlist:
                title = metadata.title or "Tiêu đề không xác định"
                artist = metadata.artist or "Nghệ sĩ không xác định"
                song_info = f"{title} - {artist}"
                formatted_playlist.append(song_info)

            return {
                "status": "success",
                "message": f"Tìm thấy {len(playlist)} bài nhạc cục bộ",
                "playlist": formatted_playlist,
                "total_count": len(playlist),
            }

        except Exception as e:
            logger.error(f"Lấy danh sách nhạc cục bộ thất bại: {e}")
            return {
                "status": "error",
                "message": f"Lấy danh sách nhạc cục bộ thất bại: {str(e)}",
                "playlist": [],
                "total_count": 0,
            }

    async def search_local_music(self, query: str) -> dict:
        """
        Tìm kiếm nhạc cục bộ.
        """
        try:
            playlist = self._scan_local_music()

            if not playlist:
                return {
                    "status": "info",
                    "message": "Không có tệp nhạc trong bộ nhớ đệm cục bộ",
                    "results": [],
                    "found_count": 0,
                }

            query = query.lower()
            results = []

            for metadata in playlist:
                # Tìm kiếm trong tiêu đề, nghệ sĩ, tên tệp
                searchable_text = " ".join(
                    filter(
                        None,
                        [
                            metadata.title,
                            metadata.artist,
                            metadata.album,
                            metadata.filename,
                        ],
                    )
                ).lower()

                if query in searchable_text:
                    title = metadata.title or "Tiêu đề không xác định"
                    artist = metadata.artist or "Nghệ sĩ không xác định"
                    song_info = f"{title} - {artist}"
                    results.append(
                        {
                            "song_info": song_info,
                            "file_id": metadata.file_id,
                            "duration": metadata.format_duration(),
                        }
                    )

            return {
                "status": "success",
                "message": f"Tìm thấy {len(results)} bài hát phù hợp trong nhạc cục bộ",
                "results": results,
                "found_count": len(results),
            }

        except Exception as e:
            logger.error(f"Tìm kiếm nhạc cục bộ thất bại: {e}")
            return {
                "status": "error",
                "message": f"Tìm kiếm thất bại: {str(e)}",
                "results": [],
                "found_count": 0,
            }

    async def play_local_song_by_id(self, file_id: str) -> dict:
        """
        Phát nhạc cục bộ theo ID tệp.
        """
        try:
            # Xây dựng đường dẫn tệp
            file_path = self.cache_dir / f"{file_id}.mp3"

            if not file_path.exists():
                # Thử các định dạng khác
                for ext in [".m4a", ".flac", ".wav", ".ogg"]:
                    alt_path = self.cache_dir / f"{file_id}{ext}"
                    if alt_path.exists():
                        file_path = alt_path
                        break
                else:
                    return {"status": "error", "message": f"Tệp cục bộ không tồn tại: {file_id}"}

            # Lấy thông tin bài hát
            metadata = MusicMetadata(file_path)
            if MUTAGEN_AVAILABLE:
                metadata.extract_metadata()

            # Cập nhật trạng thái phát
            title = metadata.title or "Tiêu đề không xác định"
            artist = metadata.artist or "Nghệ sĩ không xác định"
            self.current_song = f"{title} - {artist}"
            self.song_id = file_id
            self.total_duration = metadata.duration or 0
            self.current_url = str(file_path)  # Đường dẫn tệp cục bộ
            self._playback_target = str(file_path)
            self.current_lyric_index = -1
            self.lyrics = []  # Tệp cục bộ tạm thời không hỗ trợ lời bài hát

            started = await self._start_external_playback(start_position=0.0)
            if not started:
                return {
                    "status": "error",
                    "message": "Không tìm thấy ffplay/mpv/vlc để phát nhạc",
                }

            logger.info(f"Bắt đầu phát nhạc cục bộ: {self.current_song}")

            # Cập nhật UI
            if self.app and hasattr(self.app, "set_chat_message"):
                await self._safe_update_ui(f"Đang phát nhạc cục bộ: {self.current_song}")

            return {
                "status": "success",
                "message": f"Đang phát nhạc cục bộ: {self.current_song}",
            }

        except Exception as e:
            logger.error(f"Phát nhạc cục bộ thất bại: {e}")
            return {"status": "error", "message": f"Phát thất bại: {str(e)}"}

    # Phương thức getter thuộc tính
    async def get_current_song(self):
        return self.current_song

    async def get_is_playing(self):
        return self.is_playing

    async def get_paused(self):
        return self.paused

    async def get_duration(self):
        return self.total_duration

    async def get_position(self):
        if not self.is_playing or self.paused:
            return self.current_position

        current_pos = max(self.current_position, time.time() - self.start_play_time)
        if self.total_duration > 0:
            if current_pos >= self.total_duration:
                await self._handle_playback_finished()
                return self.total_duration
            return min(self.total_duration, current_pos)

        return current_pos

    async def get_progress(self):
        """
        Lấy phần trăm tiến độ phát.
        """
        if self.total_duration <= 0:
            return 0
        position = await self.get_position()
        return round(position * 100 / self.total_duration, 1)

    async def _handle_playback_finished(self):
        """
        Xử lý khi phát xong.
        """
        if self.is_playing:
            logger.info(f"Bài hát đã phát xong: {self.current_song}")

            await self._stop_stream_download(cleanup_file=False)

            self.is_playing = False
            self.paused = False
            if self.total_duration > 0:
                self.current_position = self.total_duration

            # Cập nhật UI hiển thị trạng thái hoàn tất
            if self.app and hasattr(self.app, "set_chat_message"):
                dur_str = self._format_time(self.total_duration)
                await self._safe_update_ui(f"Phát hoàn tất: {self.current_song} [{dur_str}]")

    # Phương thức cốt lõi
    async def search_and_play(self, song_name: str) -> dict:
        """
        Tìm kiếm và phát bài hát.
        """
        try:
            # Tìm kiếm bài hát
            song_id, url = await self._search_song(song_name)
            if not song_id or not url:
                return {"status": "error", "message": f"Không tìm thấy bài hát: {song_name}"}

            # Phát bài hát
            success = await self._play_url(url)
            if success:
                return {
                    "status": "success",
                    "message": f"Đang phát: {self.current_song}",
                }
            else:
                return {"status": "error", "message": "Phát thất bại"}

        except Exception as e:
            logger.error(f"Tìm kiếm và phát thất bại: {e}")
            return {"status": "error", "message": f"Thao tác thất bại: {str(e)}"}

    async def play_audio_url(self, audio_url: str, title: str = "") -> dict:
        """
        Phát trực tiếp từ URL audio.
        """
        url = str(audio_url or "").strip()
        if not url:
            return {"status": "error", "message": "audio_url không được để trống"}

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return {"status": "error", "message": "audio_url phải bắt đầu bằng http/https"}

        try:
            file_name = Path(parsed.path).stem.strip()
            fallback_title = file_name or "Audio URL"

            self.song_id = f"url_{hashlib.md5(url.encode('utf-8')).hexdigest()[:16]}"
            self.current_song = str(title or "").strip() or fallback_title
            self.total_duration = 0

            success = await self._play_url(url)
            if not success:
                return {"status": "error", "message": "Phát thất bại"}

            return {
                "status": "success",
                "message": f"Đang phát: {self.current_song}",
                "audio_url": url,
            }
        except Exception as e:
            logger.error(f"Phát audio URL thất bại: {e}")
            return {"status": "error", "message": f"Phát thất bại: {str(e)}"}

    async def play_stream_url(self, audio_url: str, title: str = "") -> dict:
        """
        Phát stream từ URL bằng player ngoài (ffplay/mpv/vlc).
        """
        url = str(audio_url or "").strip()
        if not url:
            return {"status": "error", "message": "audio_url không được để trống"}

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return {"status": "error", "message": "audio_url phải bắt đầu bằng http/https"}

        try:
            file_name = Path(parsed.path).stem.strip()
            fallback_title = file_name or "Audio Stream"

            self.song_id = (
                f"stream_{hashlib.md5(url.encode('utf-8')).hexdigest()[:16]}"
            )
            self.current_song = str(title or "").strip() or fallback_title
            self.total_duration = 0
            self.lyrics = []

            self.current_url = url
            self._playback_target = url

            started = await self._start_external_playback(start_position=0.0)
            if not started:
                return {
                    "status": "error",
                    "message": "Không tìm thấy ffplay/mpv/vlc để phát stream",
                }
            self.current_lyric_index = -1

            logger.info(
                f"Bắt đầu phát stream ({self._stream_player_backend}): {self.current_song}"
            )

            if self.app and hasattr(self.app, "set_chat_message"):
                await self._safe_update_ui(f"Đang phát stream: {self.current_song}")

            return {
                "status": "success",
                "message": f"Đang phát stream: {self.current_song}",
                "audio_url": url,
            }
        except Exception as e:
            await self._stop_stream_download(cleanup_file=True)
            logger.error(f"Phát stream URL thất bại: {e}")
            return {"status": "error", "message": f"Phát stream thất bại: {str(e)}"}

    async def play_pause(self) -> dict:
        """
        Chuyển đổi phát/tạm dừng.
        """
        try:
            if not self.is_playing and self.current_url:
                # Phát lại
                start_position = self.current_position if self.paused else 0.0
                success = await self._start_external_playback(start_position=start_position)
                return {
                    "status": "success" if success else "error",
                    "message": (
                        f"Bắt đầu phát: {self.current_song}" if success else "Phát thất bại"
                    ),
                }

            elif self.is_playing and self.paused:
                # Tiếp tục phát
                started = await self._start_external_playback(
                    start_position=self.current_position,
                )
                if not started:
                    return {
                        "status": "error",
                        "message": "Không thể tiếp tục stream: thiếu ffplay/mpv/vlc",
                    }

                self.paused = False
                self.start_play_time = time.time() - self.current_position

                # Cập nhật UI
                if self.app and hasattr(self.app, "set_chat_message"):
                    await self._safe_update_ui(f"Tiếp tục phát: {self.current_song}")

                return {
                    "status": "success",
                    "message": f"Tiếp tục phát: {self.current_song}",
                }

            elif self.is_playing and not self.paused:
                # Tạm dừng phát
                self.paused = True
                self.current_position = time.time() - self.start_play_time
                await self._stop_stream_download(cleanup_file=False)

                # Cập nhật UI
                if self.app and hasattr(self.app, "set_chat_message"):
                    pos_str = self._format_time(self.current_position)
                    dur_str = self._format_time(self.total_duration)
                    await self._safe_update_ui(
                        f"Đã tạm dừng: {self.current_song} [{pos_str}/{dur_str}]"
                    )

                return {"status": "success", "message": f"Đã tạm dừng: {self.current_song}"}

            else:
                return {"status": "error", "message": "Không có bài hát nào để phát"}

        except Exception as e:
            logger.error(f"Thao tác phát/tạm dừng thất bại: {e}")
            return {"status": "error", "message": f"Thao tác thất bại: {str(e)}"}

    async def stop(self) -> dict:
        """
        Dừng phát.
        """
        try:
            if not self.is_playing:
                return {"status": "info", "message": "Không có bài hát đang phát"}

            await self._stop_stream_download(cleanup_file=True)

            current_song = self.current_song
            self.is_playing = False
            self.paused = False
            self.current_position = 0

            # Cập nhật UI
            if self.app and hasattr(self.app, "set_chat_message"):
                await self._safe_update_ui(f"Đã dừng: {current_song}")

            return {"status": "success", "message": f"Đã dừng: {current_song}"}

        except Exception as e:
            logger.error(f"Dừng phát thất bại: {e}")
            return {"status": "error", "message": f"Dừng thất bại: {str(e)}"}

    async def seek(self, position: float) -> dict:
        """
        Tua đến vị trí chỉ định.
        """
        try:
            if not self.is_playing:
                return {"status": "error", "message": "Không có bài hát đang phát"}

            if self.total_duration > 0:
                position = max(0, min(position, self.total_duration))
            else:
                position = max(0, position)

            self.current_position = position
            self.start_play_time = time.time() - position

            if not self.paused:
                started = await self._start_external_playback(start_position=position)
                if not started:
                    return {
                        "status": "error",
                        "message": "Không thể tua stream: thiếu ffplay/mpv/vlc",
                    }

            # Cập nhật UI
            pos_str = self._format_time(position)
            dur_str = self._format_time(self.total_duration)
            if self.app and hasattr(self.app, "set_chat_message"):
                await self._safe_update_ui(f"Đã tua đến: {pos_str}/{dur_str}")

            return {"status": "success", "message": f"Đã tua đến: {position:.1f} giây"}

        except Exception as e:
            logger.error(f"Tua thất bại: {e}")
            return {"status": "error", "message": f"Tua thất bại: {str(e)}"}

    async def get_lyrics(self) -> dict:
        """
        Lấy lời bài hát hiện tại.
        """
        if not self.lyrics:
            return {"status": "info", "message": "Bài hát hiện tại không có lời", "lyrics": []}

        # Trích xuất văn bản lời hát, chuyển đổi thành danh sách
        lyrics_text = []
        for time_sec, text in self.lyrics:
            time_str = self._format_time(time_sec)
            lyrics_text.append(f"[{time_str}] {text}")

        return {
            "status": "success",
            "message": f"Lấy được {len(self.lyrics)} dòng lời bài hát",
            "lyrics": lyrics_text,
        }

    async def get_status(self) -> dict:
        """
        Lấy trạng thái trình phát.
        """
        position = await self.get_position()
        progress = await self.get_progress()

        return {
            "status": "success",
            "current_song": self.current_song,
            "is_playing": self.is_playing,
            "paused": self.paused,
            "duration": self.total_duration,
            "position": position,
            "progress": progress,
            "volume": self._volume_percent,
            "player_backend": self._stream_player_backend,
            "has_lyrics": len(self.lyrics) > 0,
        }

    async def set_volume(self, volume: int) -> dict:
        """Đặt âm lượng phát nhạc (0-100)."""
        try:
            volume = max(0, min(100, int(volume)))
        except (TypeError, ValueError):
            return {"status": "error", "message": "volume phải là số nguyên 0-100"}

        self._volume_percent = volume

        # Nếu mpv IPC socket có sẵn, gửi lệnh realtime không cần restart player
        if self._mpv_ipc_socket_path and self.is_playing and not self.paused:
            success = await self._send_mpv_command(
                "set_property",
                "volume",
                volume,
            )
            if success:
                return {
                    "status": "success",
                    "message": f"Đã đặt âm lượng: {self._volume_percent}%",
                    "volume": self._volume_percent,
                    "method": "ipc_realtime",
                }

        # Fallback: restart player (ffplay/vlc hoặc mpv không có IPC)
        if self.is_playing and not self.paused:
            current_pos = max(self.current_position, time.time() - self.start_play_time)
            started = await self._start_external_playback(start_position=current_pos)
            if not started:
                return {
                    "status": "error",
                    "message": "Không thể cập nhật âm lượng: thiếu ffplay/mpv/vlc",
                }

        return {
            "status": "success",
            "message": f"Đã đặt âm lượng: {self._volume_percent}%",
            "volume": self._volume_percent,
            "method": "restart" if self.is_playing else "saved",
        }

    async def get_volume(self) -> dict:
        """Lấy âm lượng hiện tại của trình phát nhạc (0-100)."""
        return {
            "status": "success",
            "volume": self._volume_percent,
        }

    # Phương thức nội bộ
    async def _search_song(self, song_name: str) -> Tuple[str, str]:
        """
        Tìm kiếm bài hát để lấy ID và URL.
        """
        try:
            query = (song_name or "").strip()
            if not query:
                return "", ""

            # Hỗ trợ nhập "Tên bài - Ca sĩ"
            artist_query = ""
            if " - " in query:
                title_part, artist_part = query.split(" - ", 1)
                query = title_part.strip()
                artist_query = artist_part.strip()

            base_search_url = str(self.config.get("SEARCH_URL", "")).rstrip("/")
            if not base_search_url:
                return "", ""

            # API mới: /api/search?q=...
            # Gộp cả tên bài + ca sĩ để tăng tỉ lệ khớp khi người dùng nhập "Tên bài - Ca sĩ"
            search_query = f"{query} {artist_query}".strip() if artist_query else query
            search_url = f"{base_search_url}/api/search"

            response = await asyncio.to_thread(
                requests.get,
                search_url,
                params={"q": search_query},
                headers=self.config["HEADERS"],
                timeout=10,
            )
            response.raise_for_status()

            data = response.json()

            # Ưu tiên format mới: {err, data: {songs: [...]}}
            songs = None
            if isinstance(data, dict):
                api_data = data.get("data")
                if isinstance(api_data, dict):
                    songs = api_data.get("songs")
                # Fallback cho format cũ: {songs: [...]}
                if songs is None:
                    songs = data.get("songs")

            if not songs:
                return "", ""

            # Chọn bài hát đầu tiên phù hợp nhất
            selected_song = songs[0]
            lower_query = query.lower()
            lower_artist = artist_query.lower()
            for item in songs:
                title = str(item.get("title", "")).lower()
                artist = str(
                    item.get("artistsNames", "")
                    or item.get("artist", "")
                ).lower()
                title_match = lower_query and lower_query in title
                artist_match = (not lower_artist) or (lower_artist in artist)
                if title_match and artist_match:
                    selected_song = item
                    break

            # API mới trả encodeId, stream tại /api/song/stream?id={encodeId}
            song_id = str(
                selected_song.get("encodeId", "")
                or selected_song.get("id", "")
            ).strip()

            # Fallback tương thích API cũ có audio_url
            if song_id:
                audio_url = f"{base_search_url}/api/song/stream?id={song_id}"
            else:
                raw_audio_url = str(selected_song.get("audio_url", "")).strip()
                if not raw_audio_url:
                    return "", ""
                audio_url = urljoin(f"{base_search_url}/", raw_audio_url)
                parsed = urlparse(audio_url)
                song_id = parse_qs(parsed.query).get("id", [""])[0]
                if not song_id:
                    song_id = str(selected_song.get("title", "")).strip()

            # Trích xuất metadata
            title = str(selected_song.get("title", "")).strip() or query
            artist = str(
                selected_song.get("artistsNames", "")
                or selected_song.get("artist", "")
            ).strip()
            duration_val = selected_song.get("duration", 0)
            try:
                self.total_duration = int(duration_val)
            except (TypeError, ValueError):
                self.total_duration = 0

            display_name = f"{title} - {artist}" if artist else title
            self.current_song = display_name
            self.song_id = song_id

            # Lấy lyric theo luồng cũ nếu có id
            if song_id:
                await self._fetch_lyrics(song_id)

            return song_id, audio_url

        except Exception as e:
            logger.error(f"Tìm kiếm bài hát thất bại: {e}")
            return "", ""

    async def _play_url(self, url: str) -> bool:
        """
        Phát URL chỉ định.
        """
        try:
            # Kiểm tra cache hoặc tải về
            file_path = await self._get_or_download_file(url)
            if not file_path:
                return False

            self.current_url = url
            self._playback_target = str(file_path)
            self.current_lyric_index = -1  # Đặt lại chỉ mục lời bài hát

            started = await self._start_external_playback(start_position=0.0)
            if not started:
                return False

            logger.info(f"Bắt đầu phát: {self.current_song}")

            # Cập nhật UI
            if self.app and hasattr(self.app, "set_chat_message"):
                await self._safe_update_ui(f"Đang phát: {self.current_song}")

            # Khởi động tác vụ cập nhật lời bài hát
            asyncio.create_task(self._lyrics_update_task())

            return True

        except Exception as e:
            logger.error(f"Phát thất bại: {e}")
            return False

    async def _start_stream_player(self, url: str, start_position: float) -> bool:
        """Khởi chạy player ngoài để phát audio bằng URL/đường dẫn local."""
        candidates = []
        ffplay_path = shutil.which("ffplay")
        if ffplay_path:
            cmd = [ffplay_path, "-nodisp", "-autoexit", "-loglevel", "error", "-vn"]
            if start_position > 0:
                cmd.extend(["-ss", f"{start_position:.3f}"])
            cmd.extend(["-volume", str(self._volume_percent)])
            cmd.append(url)
            candidates.append(("ffplay", cmd))

        mpv_path = shutil.which("mpv")
        if mpv_path:
            # MPV với IPC socket để điều khiển volume realtime
            self._mpv_ipc_socket_path = self._get_mpv_ipc_socket_path()
            cmd = [mpv_path, "--no-video", "--really-quiet", "--force-window=no"]
            if self._mpv_ipc_socket_path:
                cmd.append(f"--input-ipc-server={self._mpv_ipc_socket_path}")
            if start_position > 0:
                cmd.append(f"--start={start_position:.3f}")
            cmd.append(f"--volume={self._volume_percent}")
            cmd.append(url)
            candidates.append(("mpv", cmd))

        vlc_path = shutil.which("cvlc") or shutil.which("vlc")
        if vlc_path:
            cmd = [vlc_path, "--intf", "dummy", "--play-and-exit", "--no-video"]
            if start_position > 0:
                cmd.append(f"--start-time={start_position:.3f}")
            cmd.append(f"--gain={self._volume_percent / 100:.2f}")
            cmd.append(url)
            candidates.append(("vlc", cmd))

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        for backend, command in candidates:
            try:
                process = await asyncio.to_thread(
                    subprocess.Popen,
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )
                self._stream_player_process = process
                self._stream_player_backend = backend
                return True
            except Exception as e:
                logger.warning(f"Khởi chạy {backend} thất bại: {e}")
                # Dọn dẹp IPC socket path nếu khởi chạy backend thất bại
                if backend == "mpv":
                    self._mpv_ipc_socket_path = None

        self._stream_player_process = None
        self._stream_player_backend = ""
        self._mpv_ipc_socket_path = None
        return False

    async def _start_external_playback(self, start_position: float = 0.0) -> bool:
        """Khởi động phát nhạc bằng backend ngoài với target hiện tại."""
        target = (self._playback_target or self.current_url or "").strip()
        if not target:
            return False

        # Nếu muốn seek trên player đang hoạt động mà có IPC, dùng seek command
        if (
            self._mpv_ipc_socket_path
            and self.is_playing
            and not self.paused
            and self._stream_player_backend == "mpv"
            and start_position > 0
        ):
            success = await self._send_mpv_command(
                "seek",
                start_position,
                "absolute",
            )
            if success:
                self.current_position = start_position
                self.start_play_time = time.time() - start_position
                return True

        await self._stop_stream_download(cleanup_file=False)
        self.is_playing = False
        self.paused = False
        self._is_stream_url = False

        started = await self._start_stream_player(target, start_position=start_position)
        if not started:
            return False

        self.is_playing = True
        self.paused = False
        self.current_position = start_position
        self.start_play_time = time.time() - start_position
        self._is_stream_url = True
        self._stream_monitor_task = asyncio.create_task(self._monitor_stream_playback())
        return True

    async def _monitor_stream_playback(self) -> None:
        """Theo dõi tiến trình player ngoài và đồng bộ trạng thái phát."""
        try:
            while self._is_stream_url and self.is_playing:
                await asyncio.sleep(0.5)

                if self.paused:
                    continue

                if self._stream_player_process is None:
                    break

                if self._stream_player_process.poll() is None:
                    continue

                await self._handle_playback_finished()
                break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Giám sát stream ngoại lệ: {e}")

    async def _stop_stream_download(self, cleanup_file: bool) -> None:
        """Dừng player stream và dọn trạng thái liên quan.

        Giữ tên hàm cũ để tương thích luồng gọi hiện có.
        """

        current_task = asyncio.current_task()
        if self._stream_monitor_task is not None:
            if not self._stream_monitor_task.done():
                if self._stream_monitor_task is not current_task:
                    self._stream_monitor_task.cancel()
            if self._stream_monitor_task is not current_task:
                try:
                    await self._stream_monitor_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug(f"Kết thúc giám sát stream có cảnh báo: {e}")
            self._stream_monitor_task = None

        process = self._stream_player_process
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    await asyncio.to_thread(process.wait, 2)
            except Exception:
                try:
                    if process.poll() is None:
                        process.kill()
                except Exception:
                    pass
            self._stream_player_process = None
            self._stream_player_backend = ""

        # Dọn dẹp MPV IPC socket
        self._mpv_ipc_socket_path = None

        if cleanup_file:
            self._is_stream_url = False

    async def _get_or_download_file(self, url: str) -> Optional[Path]:
        """Lấy hoặc tải tệp.

        Kiểm tra cache trước, nếu không có trong cache thì tải về
        """
        try:
            # Sử dụng ID bài hát làm tên tệp cache
            cache_filename = f"{self.song_id}.mp3"
            cache_path = self.cache_dir / cache_filename

            # Kiểm tra cache tồn tại
            if cache_path.exists():
                logger.info(f"Sử dụng cache: {cache_path}")
                return cache_path

            # Cache không tồn tại, cần tải về
            return await self._download_file(url, cache_filename)

        except Exception as e:
            logger.error(f"Lấy tệp thất bại: {e}")
            return None

    async def _download_file(self, url: str, filename: str) -> Optional[Path]:
        """Tải tệp xuống thư mục cache.

        Tải xuống thư mục tạm thời trước, sau khi hoàn tất di chuyển vào thư mục cache chính thức
        """
        temp_path = None
        try:
            # Tạo đường dẫn tệp tạm thời
            temp_path = self.temp_cache_dir / f"temp_{int(time.time())}_{filename}"

            # Tải xuống bất đồng bộ
            response = await asyncio.to_thread(
                requests.get,
                url,
                headers=self.config["HEADERS"],
                stream=True,
                timeout=30,
            )
            response.raise_for_status()

            # Ghi vào tệp tạm thời
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            # Tải xuống hoàn tất, di chuyển vào thư mục cache chính thức
            cache_path = self.cache_dir / filename
            shutil.move(str(temp_path), str(cache_path))

            logger.info(f"Nhạc đã tải xuống và cache: {cache_path}")
            return cache_path

        except Exception as e:
            logger.error(f"Tải xuống thất bại: {e}")
            # Dọn dẹp tệp tạm thời
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                    logger.debug(f"Đã dọn dẹp tệp tải xuống tạm thời: {temp_path}")
                except Exception:
                    pass
            return None

    async def _fetch_lyrics(self, song_id: str):
        """
        Lấy lời bài hát.
        """
        try:
            # Đặt lại lời bài hát
            self.lyrics = []

            lyric_base_url = str(self.config.get("LYRIC_URL", "")).rstrip("/")
            if not lyric_base_url:
                logger.warning("LYRIC_URL chưa được cấu hình")
                return

            # API mới: GET /api/lyric?id={song_id}
            lyric_api_url = f"{lyric_base_url}/api/lyric"
            logger.info(f"URL lấy lời bài hát: {lyric_api_url}")

            response = await asyncio.to_thread(
                requests.get,
                lyric_api_url,
                params={"id": song_id},
                headers=self.config["HEADERS"],
                timeout=10,
            )
            response.raise_for_status()

            content_type = (response.headers.get("Content-Type") or "").lower()

            # Ưu tiên API mới: {data: {file: "https://...lrc"}}
            lrc_content = ""
            if "application/json" in content_type:
                data = response.json()
                if isinstance(data, dict):
                    payload = data.get("data")
                    if isinstance(payload, dict):
                        lrc_file_url = str(payload.get("file", "")).strip()
                        if lrc_file_url:
                            lrc_resp = await asyncio.to_thread(
                                requests.get,
                                lrc_file_url,
                                headers=self.config["HEADERS"],
                                timeout=10,
                            )
                            lrc_resp.raise_for_status()
                            lrc_content = lrc_resp.text or ""

                        # Fallback tương thích: API cũ trả trực tiếp content
                        if not lrc_content.strip():
                            lrc_content = str(payload.get("content", "") or "")
            else:
                lrc_content = response.text or ""

            if not lrc_content.strip():
                logger.warning("Không lấy được lời bài hát hoặc nội dung rỗng")
                return

            time_pattern = re.compile(r"\[(\d{2}):(\d{2})(?:\.(\d{1,3}))?\]")
            meta_prefixes = (
                "作词",
                "作曲",
                "编曲",
                "ti:",
                "ar:",
                "al:",
                "by:",
                "offset:",
            )

            for raw_line in lrc_content.splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                matches = list(time_pattern.finditer(line))
                if not matches:
                    continue

                text = time_pattern.sub("", line).strip()
                if not text:
                    continue

                text_lower = text.lower()
                if any(text_lower.startswith(prefix.lower()) for prefix in meta_prefixes):
                    continue

                for match in matches:
                    minutes = int(match.group(1))
                    seconds = int(match.group(2))
                    fraction = match.group(3) or "0"
                    # Chuẩn hóa phần thập phân về mili-giây (1-3 chữ số)
                    fraction_ms = int(fraction.ljust(3, "0")[:3])
                    time_sec = minutes * 60 + seconds + (fraction_ms / 1000.0)
                    self.lyrics.append((time_sec, text))

            self.lyrics.sort(key=lambda x: x[0])
            logger.info(f"Lấy lời bài hát thành công, tổng cộng {len(self.lyrics)} dòng")

        except Exception as e:
            logger.error(f"Lấy lời bài hát thất bại: {e}")

    async def _lyrics_update_task(self):
        """
        Tác vụ cập nhật lời bài hát.
        """
        if not self.lyrics:
            return

        try:
            while self.is_playing:
                if self.paused:
                    await asyncio.sleep(0.5)
                    continue

                current_time = time.time() - self.start_play_time

                # Kiểm tra xem đã phát xong chưa
                if current_time >= self.total_duration:
                    await self._handle_playback_finished()
                    break

                # Tìm lời bài hát tương ứng với thời gian hiện tại
                current_index = self._find_current_lyric_index(current_time)

                # Nếu chỉ mục lời bài hát thay đổi, cập nhật hiển thị
                if current_index != self.current_lyric_index:
                    await self._display_current_lyric(current_index)

                await asyncio.sleep(0.2)
        except Exception as e:
            logger.error(f"Tác vụ cập nhật lời bài hát ngoại lệ: {e}")

    def _find_current_lyric_index(self, current_time: float) -> int:
        """
        Tìm chỉ mục lời bài hát tương ứng với thời gian hiện tại.
        """
        # Tìm câu lời tiếp theo
        next_lyric_index = None
        for i, (time_sec, _) in enumerate(self.lyrics):
            # Thêm một độ lệch nhỏ (0.5 giây) để hiển thị lời bài hát chính xác hơn
            if time_sec > current_time - 0.5:
                next_lyric_index = i
                break

        # Xác định chỉ mục lời bài hát hiện tại
        if next_lyric_index is not None and next_lyric_index > 0:
            # Nếu tìm thấy câu tiếp theo, câu hiện tại là câu trước đó
            return next_lyric_index - 1
        elif next_lyric_index is None and self.lyrics:
            # Nếu không tìm thấy câu tiếp theo, có nghĩa là đã đến câu cuối cùng
            return len(self.lyrics) - 1
        else:
            # Trường hợp khác (ví dụ: mới bắt đầu phát)
            return 0

    async def _display_current_lyric(self, current_index: int):
        """
        Hiển thị lời bài hát hiện tại.
        """
        self.current_lyric_index = current_index

        if current_index < len(self.lyrics):
            time_sec, text = self.lyrics[current_index]

            # Thêm thông tin thời gian và tiến độ trước lời bài hát
            position_str = self._format_time(time.time() - self.start_play_time)
            duration_str = self._format_time(self.total_duration)
            display_text = f"[{position_str}/{duration_str}] {text}"

            # Cập nhật UI
            if self.app and hasattr(self.app, "set_chat_message"):
                await self._safe_update_ui(display_text)
                logger.debug(f"Hiển thị lời bài hát: {text}")

    def _extract_value(self, text: str, start_marker: str, end_marker: str) -> str:
        """
        Trích xuất giá trị từ văn bản.
        """
        start_pos = text.find(start_marker)
        if start_pos == -1:
            return ""

        start_pos += len(start_marker)
        end_pos = text.find(end_marker, start_pos)

        if end_pos == -1:
            return ""

        return text[start_pos:end_pos]

    def _format_time(self, seconds: float) -> str:
        """
        Định dạng giây thành định dạng mm:ss.
        """
        minutes = int(seconds) // 60
        seconds = int(seconds) % 60
        return f"{minutes:02d}:{seconds:02d}"

    def _get_mpv_ipc_socket_path(self) -> Optional[str]:
        """Tạo đường dẫn MPV IPC socket."""
        if os.name == "nt":  # Windows
            # Dùng pipe trên Windows
            return f"\\\\.\\pipe\\mpv-ipc-{uuid.uuid4().hex[:8]}"
        else:  # Linux/macOS
            # Dùng Unix socket
            temp_dir = tempfile.gettempdir()
            return os.path.join(temp_dir, f"mpv-ipc-{uuid.uuid4().hex[:8]}.sock")

    async def _send_mpv_command(self, *args: tuple) -> bool:
        """Gửi lệnh tới MPV qua IPC socket.
        
        Args:
            *args: lệnh và tham số (vd: "set_property", "volume", 70)
            
        Returns:
            True nếu thành công, False nếu thất bại hay socket không sẵn
        """
        if not self._mpv_ipc_socket_path:
            return False

        try:
            self._mpv_command_id += 1
            payload = {
                "command": list(args),
                "request_id": self._mpv_command_id,
            }
            json_cmd = json.dumps(payload) + "\n"

            # Gửi lệnh qua socket (non-blocking, timeout 2s)
            def send_to_mpv():
                try:
                    if os.name == "nt":  # Windows - dùng named pipe
                        import msvcrt

                        handle = msvcrt.open(self._mpv_ipc_socket_path, os.O_WRONLY)
                        os.write(handle, json_cmd.encode())
                        os.close(handle)
                    else:  # Unix - dùng socket
                        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        sock.settimeout(2)
                        sock.connect(self._mpv_ipc_socket_path)
                        sock.sendall(json_cmd.encode())
                        sock.close()
                    return True
                except Exception:
                    return False

            result = await asyncio.to_thread(send_to_mpv)
            return result
        except Exception as e:
            logger.debug(f"Gửi lệnh tới MPV thất bại: {e}")
            return False

    async def _safe_update_ui(self, message: str):
        """
        Cập nhật UI một cách an toàn.
        """
        if not self.app or not hasattr(self.app, "set_chat_message"):
            return

        try:
            self.app.set_chat_message("assistant", message)
        except Exception as e:
            logger.error(f"Cập nhật UI thất bại: {e}")

    def __del__(self):
        """
        Dọn dẹp tài nguyên.
        """
        try:
            # Nếu chương trình thoát bình thường, dọn dẹp cache tạm thời thêm một lần nữa
            self._clean_temp_cache()
        except Exception:
            # Bỏ qua lỗi, vì trong giai đoạn hủy đối tượng có thể có nhiều ngoại lệ khác nhau
            pass


# Instance trình phát nhạc toàn cục
_music_player_instance = None


def get_music_player_instance() -> MusicPlayer:
    """
    Lấy singleton trình phát nhạc.
    """
    global _music_player_instance
    if _music_player_instance is None:
        _music_player_instance = MusicPlayer()
        logger.info("[MusicPlayer] Tạo instance singleton trình phát nhạc")
    return _music_player_instance
