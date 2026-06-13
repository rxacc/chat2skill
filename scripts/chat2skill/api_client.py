"""HTTP client for the Chat2Skill cloud API. Stdlib only — no deps.

Privacy contract: the cloud runs the extraction algorithm statelessly.
Conversations and the user's LLM api key are used in memory for the one
request and are not persisted server-side. All results land back here
and are stored locally under the data home.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

SCHEMA_VERSION = "1"
DEFAULT_TIMEOUT = 180
USER_AGENT = "Chat2Skill/0.1 (+https://github.com/rexia01/chat2skill)"


class ApiError(Exception):
    pass


def extract(api_url: str, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    return _post_json(f"{api_url.rstrip('/')}/v1/extract", payload, timeout)


def project_skill(api_url: str, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    return _post_json(f"{api_url.rstrip('/')}/v1/project-skill", payload, timeout)


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    payload = {"schema_version": SCHEMA_VERSION, **payload}
    api_key = _payload_api_key(payload)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = _redact(exc.read().decode("utf-8", errors="replace")[:500], api_key)
        raise ApiError(f"HTTP {exc.code} from {url}: {detail}") from None
    except urllib.error.URLError as exc:
        raise ApiError(f"cannot reach {url}: {_redact(str(exc.reason), api_key)}") from None
    except json.JSONDecodeError:
        raise ApiError(f"invalid JSON response from {url}") from None


def _payload_api_key(payload: dict) -> Optional[str]:
    llm = payload.get("llm")
    if isinstance(llm, dict):
        return llm.get("api_key")
    return None


def _redact(text: str, api_key: Optional[str]) -> str:
    if api_key:
        text = text.replace(api_key, "***")
    return text
