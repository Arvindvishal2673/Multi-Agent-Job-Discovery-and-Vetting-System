"""NVIDIA NIM LLM client wrapper using official OpenAI SDK."""

import json
import logging
import re
import time
from typing import List, Optional

from openai import OpenAI
from . import config

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when the LLM cannot be configured or reached."""


def extract_json(text: str) -> dict:
    """Extract the first valid JSON object found in an LLM response."""
    # Remove markdown code blocks if present
    cleaned = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", text, flags=re.DOTALL)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        log.error("❌ Failed to find JSON object in LLM response:\n%s", text[:300])
        raise ValueError("No JSON object found in LLM response")
    try:
        return json.loads(match.group(0))
    except Exception as exc:
        log.error("❌ Failed to parse JSON substring: %s\nSnippet: %s", exc, match.group(0)[:300])
        raise


class NvidiaLLM:
    """NVIDIA NIM LLM client via OpenAI SDK."""

    def __init__(self, api_key: str = None, model: str = None, base_url: str = None):
        self.api_key = api_key or config.NVIDIA_API_KEY
        self.model = model or config.NVIDIA_MODEL
        self.base_url = base_url or config.NVIDIA_BASE_URL

        if not self.api_key:
            raise LLMError(
                "NVIDIA_API_KEY is not set. Get an API key at https://build.nvidia.com/ and add it to your .env file."
            )

        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )

    def _stream_completion(self, messages: List[dict], temperature: float = 1.0, top_p: float = 0.95) -> str:
        """Call NVIDIA NIM API with thinking and streaming enabled."""
        log.debug("🤖 [NVIDIA LLM Stream Request] Model: %s | Messages: %d", self.model, len(messages))
        max_retries = 3
        backoff = 2.0

        for attempt in range(max_retries):
            try:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    top_p=top_p,
                    max_tokens=16384,
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": True},
                        "reasoning_budget": 16384,
                    },
                    stream=True,
                )

                content_chunks = []
                for chunk in completion:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        log.debug("🧠 [Reasoning]: %s", reasoning)
                    if delta.content is not None:
                        content_chunks.append(delta.content)

                res_text = "".join(content_chunks)
                log.debug("🤖 [NVIDIA LLM Response] Received %d chars from %s", len(res_text), self.model)
                return res_text

            except Exception as exc:
                if attempt == max_retries - 1:
                    log.error("❌ NVIDIA Request Exception on attempt %d: %s", attempt + 1, exc)
                    raise
                log.warning("⚠️ Request exception on attempt %d: %s. Retrying in %.1fs...", attempt + 1, exc, backoff)
                time.sleep(backoff)
                backoff *= 2.0

        raise LLMError("Failed to communicate with NVIDIA NIM API.")

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        """Single-turn chat interface."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._stream_completion(messages, temperature=temperature)

    def chat_multi_turn(self, messages: List[dict], temperature: float = 0.2) -> str:
        """Multi-turn chat supporting full message history."""
        return self._stream_completion(messages, temperature=temperature)

    def chat_with_tools(
        self,
        system: str,
        user: str,
        tools: List[dict],
        temperature: float = 0.0,
    ) -> Optional[List[dict]]:
        """Tool-calling via OpenAI SDK with NVIDIA backend."""
        log.debug("🤖 [NVIDIA Tool-Calling Request] Model: %s | Tools: %d", self.model, len(tools))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
            )
            message = response.choices[0].message
            if message.tool_calls:
                formatted_calls = []
                for tc in message.tool_calls:
                    formatted_calls.append({
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    })
                log.info("🛠️ [NVIDIA Tool Selection] Selected %d tools: %s", len(formatted_calls), [fc['function']['name'] for fc in formatted_calls])
                return formatted_calls
            else:
                log.info("🛠️ [NVIDIA Tool Selection] No tool calls returned by model.")
                return None
        except Exception as exc:
            log.warning("⚠️ Native tool calling unavailable on model %s: %s. Falling back to default tools.", self.model, exc)
            return None





def get_default_llm():
    """Returns the configured NVIDIA LLM client."""
    log.info("🤖 Using NVIDIA NIM LLM (%s)", config.NVIDIA_MODEL)
    return NvidiaLLM()
