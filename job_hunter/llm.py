"""Thin wrapper around the Groq OpenAI-compatible chat completions API."""

import json
import re
import time
from typing import List, Optional

import requests

from . import config


class LLMError(RuntimeError):
    """Raised when the LLM cannot be configured or reached."""


class GroqLLM:
    """Minimal Groq chat client (no SDK dependency)."""

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or config.GROQ_API_KEY
        self.model = model or config.GROQ_MODEL
        if not self.api_key:
            raise LLMError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
            )

    def _post(self, payload: dict, max_retries: int = 6) -> dict:
        """Internal helper: POST to Groq with exponential backoff & model fallback on 429."""
        backoff = 3.0
        current_payload = dict(payload)
        for attempt in range(max_retries):
            response = requests.post(
                config.GROQ_API_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=current_payload,
                timeout=config.REQUEST_TIMEOUT,
            )
            if response.status_code == 429 and attempt < max_retries - 1:
                # If 429 persists after 2 attempts, fallback to high-throughput llama-3.1-8b-instant model
                if attempt >= 2 and current_payload.get("model") != "llama-3.1-8b-instant":
                    current_payload["model"] = "llama-3.1-8b-instant"
                
                retry_after = response.headers.get("Retry-After") or response.headers.get("x-ratelimit-reset-requests")
                match = re.search(r"([\d.]+)", str(retry_after)) if retry_after else None
                sleep_time = float(match.group(1)) if match else backoff
                time.sleep(max(sleep_time, backoff))
                backoff *= 1.5
                continue
            response.raise_for_status()
            return response.json()
        response.raise_for_status()

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        """Simple single-turn chat. Returns the assistant message content."""
        data = self._post({
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        })
        return data["choices"][0]["message"]["content"]

    def chat_multi_turn(self, messages: List[dict], temperature: float = 0.2) -> str:
        """Multi-turn chat supporting full message history (for ReAct loops).

        Args:
            messages: List of {"role": "system"|"user"|"assistant", "content": str}

        Returns:
            The assistant's reply as a plain string.
        """
        data = self._post({
            "model": self.model,
            "temperature": temperature,
            "messages": messages,
        })
        return data["choices"][0]["message"]["content"]

    def chat_with_tools(
        self,
        system: str,
        user: str,
        tools: List[dict],
        temperature: float = 0.0,
    ) -> Optional[List[dict]]:
        """Tool-calling (function-calling) via Groq's OpenAI-compatible API.

        Sends the tools schema to the LLM and returns the list of tool_calls
        selected by the model, or None if the model replied with plain text.

        Args:
            system:      System prompt.
            user:        User message.
            tools:       List of OpenAI-format tool schemas.
            temperature: Lower is more deterministic (default 0.0 for planning).

        Returns:
            List of tool_call dicts like:
            [{"id": "...", "function": {"name": "...", "arguments": "{}"}}]
            or None if the model chose not to call any tool.
        """
        data = self._post({
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": tools,
            "tool_choice": "auto",
        })
        message = data["choices"][0]["message"]
        return message.get("tool_calls") or None


def extract_json(text: str) -> dict:
    """Extract the first JSON object found in an LLM response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(match.group(0))

