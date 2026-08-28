"""
Plugin AI Chat - Hỏi AI và nhận phản hồi
Tính năng:
- Lắng nghe từ wake word (audio input)
- Nhận request từ HTTP API
- Gọi Custom API endpoint (như ChatGPT, Ollama, v.v.)
- Phát audio response qua TTS
"""
import asyncio
import json
from typing import Any, Optional

import httpx

from src.plugins.base import Plugin
from src.utils.config_manager import ConfigManager
from src.utils.common_utils import _play_custom_tts
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class AIChatPlugin(Plugin):
    name = "ai_chat"

    def __init__(self) -> None:
        super().__init__()
        self.application: Optional[Any] = None
        self.config = ConfigManager.get_instance()
        
        # Lấy cấu hình
        self.enabled = self.config.get_config("AI_CHAT_OPTIONS.ENABLED", True)
        self.api_url = self.config.get_config("AI_CHAT_OPTIONS.API_URL", "")
        self.api_key = self.config.get_config("AI_CHAT_OPTIONS.API_KEY", None)
        self.model = self.config.get_config("AI_CHAT_OPTIONS.MODEL", "gpt-3.5-turbo")
        self.max_tokens = self.config.get_config("AI_CHAT_OPTIONS.MAX_TOKENS", 2000)
        self.temperature = self.config.get_config("AI_CHAT_OPTIONS.TEMPERATURE", 0.7)
        self.use_audio_input = self.config.get_config("AI_CHAT_OPTIONS.USE_AUDIO_INPUT", True)
        self.use_audio_output = self.config.get_config("AI_CHAT_OPTIONS.USE_AUDIO_OUTPUT", True)
        self.system_prompt = self.config.get_config(
            "AI_CHAT_OPTIONS.SYSTEM_PROMPT",
            "Bạn là một trợ lý AI hữu ích, thân thiện và lịch sự. Hãy trả lời câu hỏi một cách ngắn gọn và rõ ràng."
        )
        
        # HTTP client
        self.http_client: Optional[httpx.AsyncClient] = None
        self._conversation_history: list[dict] = []
        self._max_history = 10  # Lưu tối đa 10 tin nhắn gần nhất
        
        if not self.enabled:
            logger.info("AI Chat plugin is disabled")
        elif not self.api_url:
            logger.warning("AI_CHAT_OPTIONS.API_URL not configured")

    async def setup(self, app: Any) -> None:
        """Chuẩn bị plugin"""
        self.application = app
        logger.info("AI Chat plugin setup completed")

    async def start(self) -> None:
        """Khởi động plugin"""
        if not self.enabled or not self.api_url:
            return
            
        # Tạo HTTP client
        self.http_client = httpx.AsyncClient(timeout=9999)
        logger.info(f"AI Chat plugin started (API: {self.api_url})")
        await asyncio.sleep(2)  # Đảm bảo async context
        await self.handle_ai_query("Bạn hãy hỏi tôi chào tôi, và hỏi 1 câu ngắn gọn được không, ví dụ bạn có khoẻ không, hoặc thời tiết hay gì đó, hãy trả lời tôi bằng tiếng Việt nhé ngắn gọn thôi, không dài dòng random chủ đề câu hỏi")
                      
    async def stop(self) -> None:
        """Dừng plugin"""
        if self.http_client:
            await self.http_client.aclose()
            logger.info("AI Chat plugin stopped")

    async def on_incoming_json(self, message: Any) -> None:
        """Xử lý tin nhắn JSON từ server"""
        if not isinstance(message, dict):
            return
        
        msg_type = message.get("type", "")
        
        # Nếu server gửi "ask_ai" command
        if msg_type == "ask_ai":
            query = message.get("text", "")
            if query:
                await self.handle_ai_query(query)

    async def on_incoming_audio(self, data: bytes) -> None:
        """Xử lý audio từ server (speech-to-text)"""
        # Nếu nhận được audio từ wake word detection
        # Có thể xử lý speech-to-text ở đây
        pass
    
    async def test_api_connection(self) -> bool:
        logger.info(f"Testing AI API connection to {self.api_url}")
        return True

    async def handle_ai_query(self, query: str) -> None:
        """Xử lý câu hỏi từ AI"""
        if not self.enabled or not self.api_url:
            logger.warning("AI Chat not properly configured")
            return
        
        try:
            logger.info(f"🤖 AI Query: {query}")
            
            # Gọi API
            response_text = await self._call_api(query)
            
            if response_text:
                logger.info(f"💬 AI Response: {response_text}")
                
                # Phát audio response
                if self.use_audio_output and self.application:
                    await self._speak_response(response_text)
                
                # Thêm vào lịch sử conversation
                self._add_to_history(query, response_text)
                
        except Exception as e:
            logger.error(f"❌ Error handling AI query: {e}", exc_info=True)

    async def _call_api(self, query: str) -> Optional[str]:
        """Gọi API để lấy response"""
        if not self.http_client:
            logger.error("HTTP client not initialized")
            return None
        
        try:
            # Tạo payload - hỗ trợ định dạng chuẩn OpenAI
            payload = {
                # "model": self.model,
                # "messages": [
                #     {"role": "system", "content": self.system_prompt},
                #     *self._conversation_history,
                #     {"role": "user", "content": query}
                # ],
                "contents": [
                    {
                        "parts": [
                        {
                            "text": query
                        }
                        ]
                    }
                ]
                # "max_tokens": self.max_tokens,
                # "temperature": self.temperature,
            }
            
            # Thêm API key nếu có
            headers = {
                "Content-Type": "application/json",
                "X-goog-api-key": self.api_key if self.api_key else "",
            }
            # if self.api_key:
            #     headers["Authorization"] = f"Bearer {self.api_key}"
            
            logger.debug(f"Calling API: {self.api_url}")
            response = await self.http_client.post(
                self.api_url,
                json=payload,
                headers=headers,
            )
            
            response.raise_for_status()
            
            # Parse response - hỗ trợ định dạng OpenAI
            data = response.json()
            if "candidates" in data:
                try:
                    content = data["candidates"][0].get("content", {})
                    parts = content.get("parts", [])
                    if parts and isinstance(parts, list):
                        return str(parts[0].get("text", "")).strip()
                    return str(content.get("text", "")).strip()
                except Exception:
                    return str(data.get("candidates", [{}])[0]).strip()
            # Cách 1: Định dạng OpenAI
            if "choices" in data:
                return data["choices"][0]["message"]["content"].strip()
            
            # Cách 2: Định dạng { "response": "..." }
            if "response" in data:
                return data["response"].strip()
            
            # Cách 3: Định dạng { "text": "..." }
            if "text" in data:
                return data["text"].strip()
            
            # Cách 4: Định dạng { "message": "..." }
            if "message" in data:
                return data["message"].strip()
            
            logger.warning(f"Unexpected API response format: {data}")
            return "Định dạng phản hồi từ API không hợp lệ."
            
        except httpx.TimeoutException:
            logger.error("API request timeout")
            return "Quá thời gian chờ phản hồi từ API."
        except httpx.HTTPError as e:
            logger.error(f"API request error: {e}")
            return "Có lỗi khi gọi API AI. Vui lòng thử lại sau."
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse API response: {e}")
            return "Phản hồi từ API không hợp lệ."

    async def _speak_response(self, text: str) -> None:
        """Phát audio response qua TTS (audio plugin hoặc mcp)"""
        if not self.application or not self.use_audio_output:
            return
        try:
            # Thử plugin audio trước
            audio_plugin = self.application.plugins.get_plugin("audio") if self.application.plugins else None
            if audio_plugin and hasattr(audio_plugin, "play_tts"):
                await audio_plugin.play_tts(text)
                logger.info(f"📢 TTS via audio plugin: {text}")
                return
            # Dùng custom TTS từ common_utils
            try:
                import asyncio
                await asyncio.to_thread(_play_custom_tts, text)
                logger.info(f"📢 TTS via custom_tts: {text}")
                return
            except Exception as e:
                logger.warning(f"Custom TTS failed: {e}")
            # Fallback qua mcp plugin
            mcp_plugin = self.application.plugins.get_plugin("mcp") if self.application.plugins else None
            if mcp_plugin and hasattr(mcp_plugin, "call_tool"):
                await mcp_plugin.call_tool("text_to_speech", text)
                logger.info(f"📢 TTS via mcp: {text}")
                return
            logger.info(f"📢 [Audio - no TTS plugin] {text}")
        except Exception as e:
            logger.error(f"Error speaking response: {e}")

    def _add_to_history(self, query: str, response: str) -> None:
        """Thêm tin nhắn vào lịch sử conversation"""
        self._conversation_history.append({"role": "user", "content": query})
        self._conversation_history.append({"role": "assistant", "content": response})
        
        # Giữ lại tối đa MAX_HISTORY tin nhắn gần nhất
        if len(self._conversation_history) > self._max_history * 2:
            self._conversation_history = self._conversation_history[-self._max_history * 2:]

    def clear_history(self) -> None:
        """Xóa lịch sử conversation"""
        self._conversation_history.clear()
        logger.info("Conversation history cleared")

    def get_history(self) -> list[dict]:
        """Lấy lịch sử conversation"""
        return self._conversation_history.copy()
