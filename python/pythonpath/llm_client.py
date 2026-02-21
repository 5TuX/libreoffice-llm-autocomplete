"""LLM client using only stdlib — supports Anthropic and OpenAI-compatible APIs."""

import json
import ssl
import urllib.request


SYSTEM_PROMPT = (
    "You are a text autocomplete engine. Continue the given text naturally and concisely. "
    "Output ONLY the continuation, nothing else. No explanation. Max 2 sentences."
)


class LLMClient:
    def __init__(self, api_key: str, model: str, base_url: str, max_tokens: int = 80):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self._is_anthropic = "anthropic.com" in base_url.lower()

    def complete(self, context_text: str) -> str:
        """Call API synchronously, return completion string. Raises on error."""
        if self._is_anthropic:
            return self._anthropic_complete(context_text)
        return self._openai_complete(context_text)

    def _anthropic_complete(self, context_text: str) -> str:
        url = f"{self.base_url}/messages"
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0.3,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": context_text}],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        data = self._post(url, payload, headers)
        return data["content"][0]["text"].rstrip()

    def _openai_complete(self, context_text: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context_text},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = self._post(url, payload, headers)
        return data["choices"][0]["message"]["content"].rstrip()

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
