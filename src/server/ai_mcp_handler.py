"""
Kết nối Gemini Interactions API với McpServer local.

Luồng:
  user text -> Gemini /interactions voi tool list -> tool_use -> McpServer.call -> Gemini summarize -> text
"""
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from src.utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


def _cfg(key: str, default=None):
    return ConfigManager.get_instance().get_config(key, default)


def _build_fn_decls(mcp_tools) -> List[Dict]:
    result = []
    for t in mcp_tools:
        schema = t.to_json()
        result.append({
            "type": "function",
            "name": schema["name"].replace(".", "__"),
            "description": schema["description"][:1024],
            "parameters": schema["inputSchema"],
        })
    return result


class AIMcpHandler:
    """Goi Gemini Interactions API voi MCP tools."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        # API dung stateful id de tiep tuc conversation, khong can gui history
        self._interaction_id: Optional[str] = None

    async def _ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def reset_conversation(self):
        """Bat dau conversation moi."""
        self._interaction_id = None

    async def handle(self, query: str) -> str:
        api_url: str = _cfg("AI_CHAT_OPTIONS.API_URL", "")
        api_key: str = _cfg("AI_CHAT_OPTIONS.API_KEY", "") or ""
        model: str = _cfg("AI_CHAT_OPTIONS.MODEL", "gemini-3.5-flash-lite")
        system_prompt: str = _cfg(
            "AI_CHAT_OPTIONS.SYSTEM_PROMPT",
            "Ban la tro ly AI huu ich, hay tra loi ngan gon bang tieng Viet.",
        )

        if not api_url:
            return "AI chua duoc cau hinh (thieu AI_CHAT_OPTIONS.API_URL)."

        from src.mcp.mcp_server import McpServer
        mcp = McpServer.get_instance()
        fn_decls = _build_fn_decls(mcp.tools)

        try:
            answer, new_id = await self._run_loop(
                api_url, api_key, model, system_prompt, query,
                self._interaction_id, fn_decls, mcp
            )
            self._interaction_id = new_id
        except Exception as e:
            logger.error(f"[AIMcpHandler] error: {e}", exc_info=True)
            answer = f"Loi goi AI: {e}"

        return answer

    async def _run_loop(
        self,
        api_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        user_input: str,
        interaction_id: Optional[str],
        fn_decls: List[Dict],
        mcp,
        max_rounds: int = 5,
    ) -> tuple:
        """Tra ve (answer_text, interaction_id)."""
        await self._ensure_client()
        headers = {
            "Content-Type": "application/json",
            "X-goog-api-key": api_key,
        }

        current_input = user_input
        current_id = interaction_id

        for _ in range(max_rounds):
            payload: Dict[str, Any] = {
                "model": model,
                "input": current_input,
            }
            # Tiep tuc conversation stateful qua id
            if current_id:
                payload["id"] = current_id
            if system_prompt and not current_id:
                # Chi gui system_instruction cho turn dau tien
                payload["system_instruction"] = system_prompt
            if fn_decls:
                payload["tools"] = fn_decls

            logger.debug(f"[AIMcpHandler] POST input={current_input[:200]} id={current_id}")
            resp = await self._client.post(api_url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.error(f"[AIMcpHandler] HTTP {resp.status_code}: {resp.text[:500]}")
                resp.raise_for_status()
            data = resp.json()

            current_id = data.get("id", current_id)
            steps = data.get("steps", [])
            tool_uses = [s for s in steps if s.get("type") == "function_call"]
            model_texts = [
                c["text"]
                for s in steps
                if s.get("type") == "model_output"
                for c in s.get("content", [])
                if c.get("type") == "text" and c.get("text")
            ]

            if not tool_uses:
                return " ".join(model_texts).strip(), current_id

            tool_results = []
            for tu in tool_uses:
                tool_name = str(tu.get("name", "")).replace("__", ".")
                args = tu.get("arguments", tu.get("input", {})) or {}
                result = await _call_mcp_tool(mcp, tool_name, args)
                logger.debug(f"[AIMcpHandler] tool {tool_name} -> {result[:100]}")
                tool_results.append(result)

            current_input = "\n".join(tool_results)

        return "Da dat gioi han vong lap tool calling.", current_id


async def _call_mcp_tool(mcp, tool_name: str, args: Dict) -> str:
    tool = next((t for t in mcp.tools if t.name == tool_name), None)
    if not tool:
        return f"Tool khong ton tai: {tool_name}"
    try:
        raw = await tool.call(args)
        parsed = json.loads(raw)
        parts = parsed.get("content", [])
        return " ".join(p.get("text", "") for p in parts if "text" in p)
    except Exception as e:
        return f"Tool error: {e}"


_handler: Optional[AIMcpHandler] = None


def get_ai_mcp_handler() -> AIMcpHandler:
    global _handler
    if _handler is None:
        _handler = AIMcpHandler()
    return _handler
