from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_env_file() -> None:
    """Load key/value pairs from env/.env if it exists."""
    env_file = _repo_root() / "env" / ".env"
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """
    Call DeepSeek Chat Completions API using env-based configuration.

    Required env:
    - DEEPSEEK_API_KEY

    Optional env:
    - DEEPSEEK_BASE_URL (default: https://api.deepseek.com)
    - DEEPSEEK_MODEL (default: deepseek-chat)
    - LLM_TEMPERATURE (default: 0)
    - LLM_TIMEOUT_SECONDS (default: 120)
    """
    _load_env_file()

    api_key = _required_env("DEEPSEEK_API_KEY")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    temperature = float(os.getenv("LLM_TEMPERATURE", "0"))
    timeout_seconds = int(os.getenv("LLM_TIMEOUT_SECONDS", "120"))

    endpoint = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    request = Request(
        url=endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        logger.error("DeepSeek API HTTP error %s: %s", exc.code, error_body)
        raise RuntimeError(f"DeepSeek API HTTP error {exc.code}: {error_body}") from exc
    except URLError as exc:
        logger.error("DeepSeek API connection error: %s", exc)
        raise RuntimeError(f"DeepSeek API connection error: {exc}") from exc

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON response from DeepSeek API")
        raise RuntimeError("Invalid JSON response from DeepSeek API") from exc

    choices = parsed.get("choices") or []
    if not choices:
        raise RuntimeError(f"DeepSeek API returned no choices: {parsed}")

    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        ).strip()
    else:
        text = ""

    if not text:
        raise RuntimeError(f"DeepSeek API returned empty content: {parsed}")

    return text
