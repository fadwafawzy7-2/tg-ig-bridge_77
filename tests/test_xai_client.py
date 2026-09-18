import pytest

from app.ai.ai_client import XAIClient, AIProviderError


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
            raise httpx.HTTPStatusError("bad", request=request, response=self)

    def json(self):
        return self._data


@pytest.mark.asyncio
async def test_xai_client_parses_chat_completion(monkeypatch):
    import httpx

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return FakeResponse({"choices": [{"message": {"content": "caption"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    client = XAIClient(api_key="k", model="grok-4.6", base_url="https://api.x.ai/v1", timeout_seconds=5)
    assert await client.complete(system_prompt="s", user_prompt="u", max_tokens=100) == "caption"


@pytest.mark.asyncio
async def test_xai_client_rejects_empty_shape(monkeypatch):
    import httpx

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return FakeResponse({"choices": []})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    client = XAIClient(api_key="k", model="grok-4.6", base_url="https://api.x.ai/v1", timeout_seconds=5)
    with pytest.raises(AIProviderError):
        await client.complete(system_prompt="s", user_prompt="u", max_tokens=100)
