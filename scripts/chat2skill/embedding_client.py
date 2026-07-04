"""OpenAI-compatible embedding client for local retrieval."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

DEFAULT_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_LOCAL_EMBEDDING_MODEL = "Snowflake/snowflake-arctic-embed-xs"
DEFAULT_LOCAL_EMBEDDING_DIMENSIONS = 384
DEFAULT_TIMEOUT = 60


class EmbeddingClientError(Exception):
    pass


class EmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = DEFAULT_EMBEDDING_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_EMBEDDING_BASE_URL).rstrip("/")
        self.embedding_model = model or DEFAULT_EMBEDDING_MODEL
        self.timeout = timeout

    def embed(self, text: str, model: Optional[str] = None) -> list[float]:
        payload = {
            "model": model or self.embedding_model,
            "input": text,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Chat2Skill/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise EmbeddingClientError(f"embedding HTTP {exc.code}: {_redact(detail, self.api_key)}") from None
        except urllib.error.URLError as exc:
            raise EmbeddingClientError(f"embedding request failed: {_redact(str(exc.reason), self.api_key)}") from None
        except json.JSONDecodeError:
            raise EmbeddingClientError("embedding response was not valid JSON") from None

        try:
            return [float(value) for value in data["data"][0]["embedding"]]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise EmbeddingClientError("embedding response did not include a vector") from exc


class LocalTransformersEmbeddingClient:
    """Local transformers.js embedding client using the GitNexus default model."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_LOCAL_EMBEDDING_MODEL,
        dimensions: int = DEFAULT_LOCAL_EMBEDDING_DIMENSIONS,
        node_path: Optional[str] = None,
        timeout: int = 120,
    ):
        self.embedding_model = model or DEFAULT_LOCAL_EMBEDDING_MODEL
        self.dimensions = int(dimensions or DEFAULT_LOCAL_EMBEDDING_DIMENSIONS)
        self.node_path = node_path or os.environ.get("CHAT2SKILL_NODE_PATH") or "node"
        self.timeout = timeout
        self.helper_path = Path(__file__).with_name("local_embedding_helper.mjs")

    def embed(self, text: str, model: Optional[str] = None) -> list[float]:
        vectors = self.embed_many([text], model=model)
        return vectors[0] if vectors else []

    def embed_many(self, texts: list[str], model: Optional[str] = None) -> list[list[float]]:
        payload = {
            "texts": texts,
            "model": model or self.embedding_model,
            "dimensions": self.dimensions,
        }
        try:
            proc = subprocess.run(
                [self.node_path, str(self.helper_path)],
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise EmbeddingClientError("local embedding requires node in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise EmbeddingClientError("local embedding timed out") from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            raise EmbeddingClientError(f"local embedding failed: {detail}")

        try:
            data = json.loads(proc.stdout)
            vectors = data["vectors"]
            return [[float(value) for value in vector] for vector in vectors]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingClientError("local embedding response did not include vectors") from exc


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text
