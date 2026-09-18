"""Small async Telegram Bot API client using the project's existing httpx dependency."""
from __future__ import annotations
import httpx

class TelegramBotAPIError(RuntimeError):
    pass

class TelegramBotAPI:
    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self._token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self.client.aclose()

    async def call(self, method: str, **payload):
        response = await self.client.post(f"{self.base_url}/{method}", json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            # Telegram returns HTTP 400 for "message is not modified" when
            # editing a message to content identical to what's already
            # shown. That's a harmless no-op (e.g. tapping the same button
            # twice), not a real failure — swallow only this specific case.
            try:
                body = response.json()
            except ValueError:
                body = {}
            description = str(body.get("description", ""))
            if "message is not modified" in description.lower():
                return body.get("result")
            raise
        data = response.json()
        if not data.get("ok"):
            if "message is not modified" in str(data.get("description", "")).lower():
                return data.get("result")
            raise TelegramBotAPIError(data.get("description", "Telegram API error"))
        return data.get("result")

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.call("sendMessage", **payload)

    async def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> dict:
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.call("editMessageText", **payload)

    async def answer_callback(self, callback_id: str, text: str | None = None) -> bool:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        return bool(await self.call("answerCallbackQuery", **payload))

    async def delete_webhook(self) -> bool:
        return bool(await self.call("deleteWebhook", drop_pending_updates=False))

    async def set_my_commands(self, commands: list[dict]) -> bool:
        # Registers Telegram's native "/" command list so it shows each
        # command with its description as soon as the person types "/" -
        # no inline-keyboard menu needed for discovery.
        return bool(await self.call("setMyCommands", commands=commands))

    async def get_updates(self, offset: int | None = None, timeout: int = 20) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return await self.call("getUpdates", **payload)

    async def get_file(self, file_id: str) -> dict:
        """Returns Telegram's file descriptor (includes file_path, needed for download_file_bytes)."""
        return await self.call("getFile", file_id=file_id)

    async def download_file_bytes(self, file_path: str) -> bytes:
        """Download the raw bytes of a file previously resolved via get_file()."""
        url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
        response = await self.client.get(url)
        response.raise_for_status()
        return response.content
