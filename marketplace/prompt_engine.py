"""
Prompt-as-API engine — execute provider-defined prompt templates via Claude API.

Providers create paid API endpoints by writing a prompt template + selecting a model.
Buyer agents send input, platform renders the template, calls Claude, returns the result.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic

logger = logging.getLogger("acf.prompt_engine")

# Supported models and their per-call base costs (approximate, for min price enforcement)
SUPPORTED_MODELS = {
    "claude-haiku-4-5": {"base_cost": 0.001, "min_price": 0.005, "max_tokens_default": 1024},
    "claude-sonnet-4-6": {"base_cost": 0.01, "min_price": 0.02, "max_tokens_default": 2048},
}

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_TIMEOUT = 30.0
MAX_INPUT_LENGTH = 10_000  # characters
MAX_SYSTEM_PROMPT_LENGTH = 50_000  # characters


@dataclass(frozen=True)
class PromptConfig:
    """Provider-defined prompt service configuration."""
    model: str = DEFAULT_MODEL
    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 1024

    def validate(self) -> list[str]:
        """Return list of validation errors (empty = valid)."""
        errors = []
        if self.model not in SUPPORTED_MODELS:
            errors.append(f"Unsupported model: {self.model}. Supported: {list(SUPPORTED_MODELS.keys())}")
        if not self.system_prompt or not self.system_prompt.strip():
            errors.append("system_prompt is required")
        if len(self.system_prompt) > MAX_SYSTEM_PROMPT_LENGTH:
            errors.append(f"system_prompt exceeds {MAX_SYSTEM_PROMPT_LENGTH} characters")
        if not (0.0 <= self.temperature <= 1.0):
            errors.append("temperature must be between 0.0 and 1.0")
        if not (1 <= self.max_tokens <= 4096):
            errors.append("max_tokens must be between 1 and 4096")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptConfig:
        return cls(
            model=data.get("model", DEFAULT_MODEL),
            system_prompt=data.get("system_prompt", ""),
            temperature=float(data.get("temperature", 0.7)),
            max_tokens=int(data.get("max_tokens", 1024)),
        )


@dataclass(frozen=True)
class PromptResult:
    """Result of a prompt execution."""
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    latency_ms: int
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def sanitize_input(user_input: str) -> str:
    """Sanitize user input to mitigate prompt injection.

    Strategy: strip control characters and excessive whitespace.
    The system prompt is always set by the provider (not user-controllable),
    so injection risk is limited to the user message content.
    """
    if not isinstance(user_input, str):
        return str(user_input)
    # Remove null bytes and control characters (except newline/tab)
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', user_input)
    # Collapse excessive whitespace (>3 consecutive newlines)
    cleaned = re.sub(r'\n{4,}', '\n\n\n', cleaned)
    return cleaned.strip()


def _get_client(api_key: str | None = None) -> anthropic.Anthropic:
    """Get Anthropic client using provider's API key (Plan A: BYOK)."""
    if not api_key:
        raise PromptEngineError("Provider API key is required for prompt services")
    return anthropic.Anthropic(api_key=api_key)


class PromptEngineError(Exception):
    """Error during prompt execution."""
    pass


def execute_prompt(config: PromptConfig, user_input: str, *, provider_api_key: str | None = None) -> PromptResult:
    """Execute a prompt template with user input via Claude API.

    Plan A (BYOK): Provider supplies their own Anthropic API key.
    Platform has zero API cost — only routes and collects commission.

    Args:
        config: Provider-defined prompt configuration.
        user_input: Buyer-provided input text.
        provider_api_key: Provider's Anthropic API key (required).

    Returns:
        PromptResult with response content and token usage.

    Raises:
        PromptEngineError: On validation or API errors.
    """
    # Validate config
    errors = config.validate()
    if errors:
        raise PromptEngineError(f"Invalid prompt config: {'; '.join(errors)}")

    # Validate and sanitize input
    if not user_input or not user_input.strip():
        raise PromptEngineError("Input text is required")
    if len(user_input) > MAX_INPUT_LENGTH:
        raise PromptEngineError(f"Input exceeds maximum length of {MAX_INPUT_LENGTH} characters")

    clean_input = sanitize_input(user_input)

    # Call Claude API using provider's key (BYOK)
    client = _get_client(provider_api_key)
    start_time = time.monotonic()

    try:
        response = client.messages.create(
            model=config.model,
            max_tokens=config.max_tokens,
            system=config.system_prompt,
            temperature=config.temperature,
            messages=[{"role": "user", "content": clean_input}],
            timeout=DEFAULT_TIMEOUT,
        )
    except anthropic.AuthenticationError:
        raise PromptEngineError("Provider API key is invalid or revoked")
    except anthropic.RateLimitError:
        raise PromptEngineError("Provider's Claude API rate limit reached, please retry later")
    except anthropic.APIConnectionError:
        raise PromptEngineError("Failed to connect to Claude API")
    except anthropic.BadRequestError as e:
        msg = str(e)
        if "credit balance" in msg.lower() or "billing" in msg.lower():
            raise PromptEngineError("Provider's Claude API credits exhausted. Contact the service provider.")
        logger.error("Claude API bad request: %s", msg)
        raise PromptEngineError("Invalid prompt configuration")
    except anthropic.APIStatusError as e:
        logger.error("Claude API error: %s %s", e.status_code, e.message)
        raise PromptEngineError(f"Claude API error: {e.status_code}")
    except Exception as e:
        logger.error("Unexpected prompt engine error: %s", e)
        raise PromptEngineError("Internal prompt engine error")

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    # Extract response text
    content = ""
    for block in response.content:
        if block.type == "text":
            content += block.text

    return PromptResult(
        content=content,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        model=config.model,
        latency_ms=elapsed_ms,
    )


async def execute_prompt_async(
    config: PromptConfig, user_input: str, *, provider_api_key: str | None = None
) -> PromptResult:
    """Async version — runs execute_prompt in a thread to avoid blocking the event loop."""
    import asyncio
    import functools
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        functools.partial(execute_prompt, config, user_input, provider_api_key=provider_api_key),
    )
