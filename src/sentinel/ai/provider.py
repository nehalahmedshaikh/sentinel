"""HTTPX-backed OpenAI-compatible chat completion provider."""

from typing import Protocol

import httpx

from sentinel.ai.contracts import ChatCompletion, ChatRequest


class ProviderError(Exception):
    """Provider transport or response failure safe to expose as a classification."""


class AIProvider(Protocol):
    def complete(self, request: ChatRequest) -> ChatCompletion: ...


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        *,
        timeout_seconds: float = 30,
        client: httpx.Client | None = None,
    ) -> None:
        self.endpoint = base_url.rstrip("/")
        if not self.endpoint.endswith("/chat/completions"):
            suffix = (
                "/chat/completions" if self.endpoint.endswith("/v1") else "/v1/chat/completions"
            )
            self.endpoint += suffix
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def complete(self, request: ChatRequest) -> ChatCompletion:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = self.client.post(
                self.endpoint, json=request.model_dump(mode="json"), headers=headers
            )
            response.raise_for_status()
            if len(response.content) > 1_000_000:
                raise ProviderError("ResponseTooLarge")
            return ChatCompletion.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(type(exc).__name__) from exc
