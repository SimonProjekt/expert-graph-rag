"""Embedding backend abstractions and implementations."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


class EmbeddingBackendError(Exception):
    """Raised when embedding backend initialization or inference fails."""


class EmbeddingBackend(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text."""


@dataclass
class LocalSentenceTransformerBackend:
    model_name: str
    embedding_dim: int
    allow_hash_fallback: bool = True

    _model: object | None = None

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingBackendError(
                "sentence-transformers is required for local embeddings. "
                "Install project dependencies including sentence-transformers."
            ) from exc

        try:
            self._model = SentenceTransformer(self.model_name)
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingBackendError(
                f"Failed to load local sentence-transformers model '{self.model_name}'."
            ) from exc

        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            model = self._load_model()
        except EmbeddingBackendError:
            if not self.allow_hash_fallback:
                raise
            logger.warning(
                "Falling back to deterministic hash embeddings because local model could not load."
            )
            return [_hash_embedding(text, self.embedding_dim) for text in texts]

        try:
            matrix = model.encode(
                texts,
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=False,
            )
        except Exception as exc:  # noqa: BLE001
            if not self.allow_hash_fallback:
                raise EmbeddingBackendError(
                    "Local sentence-transformers inference failed."
                ) from exc
            logger.warning(
                "Falling back to deterministic hash embeddings because local inference failed."
            )
            return [_hash_embedding(text, self.embedding_dim) for text in texts]

        vectors: list[list[float]] = []
        for row in matrix:
            raw = [float(value) for value in row.tolist()]
            vectors.append(_resize_vector(raw, self.embedding_dim))
        return vectors


@dataclass
class OpenAIEmbeddingBackend:
    api_key: str
    model_name: str
    embedding_dim: int

    _client: object | None = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        if not self.api_key:
            raise EmbeddingBackendError("OPENAI_API_KEY is required for OpenAI embedding backend.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise EmbeddingBackendError(
                "openai package is required for OpenAI embedding backend."
            ) from exc

        self._client = OpenAI(api_key=self.api_key)
        return self._client

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._get_client()
        try:
            response = client.embeddings.create(
                model=self.model_name,
                input=texts,
                dimensions=self.embedding_dim,
            )
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingBackendError("OpenAI embedding request failed.") from exc

        vectors: list[list[float]] = []
        for item in response.data:
            raw = [float(value) for value in item.embedding]
            vectors.append(_resize_vector(raw, self.embedding_dim))
        return vectors


def get_embedding_backend(
    *,
    backend_name: str,
    embedding_dim: int,
    local_model_name: str,
    openai_api_key: str,
    openai_model_name: str,
    allow_hash_fallback: bool = True,
) -> EmbeddingBackend:
    normalized = (backend_name or "auto").strip().lower()
    if normalized not in {"auto", "local", "openai"}:
        raise EmbeddingBackendError(
            f"Invalid embedding backend: {backend_name!r}. Use one of: auto, local, openai."
        )

    if normalized == "auto":
        normalized = "openai" if openai_api_key else "local"

    if normalized == "local":
        return LocalSentenceTransformerBackend(
            model_name=local_model_name,
            embedding_dim=embedding_dim,
            allow_hash_fallback=allow_hash_fallback,
        )

    return OpenAIEmbeddingBackend(
        api_key=openai_api_key,
        model_name=openai_model_name,
        embedding_dim=embedding_dim,
    )


def _resize_vector(values: list[float], dimensions: int) -> list[float]:
    if dimensions <= 0:
        raise EmbeddingBackendError("embedding dimensions must be greater than zero.")

    if len(values) == dimensions:
        return values

    if len(values) > dimensions:
        return values[:dimensions]

    return values + [0.0] * (dimensions - len(values))


def _hash_embedding(text: str, dimensions: int) -> list[float]:
    if dimensions <= 0:
        raise EmbeddingBackendError("embedding dimensions must be greater than zero.")

    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    for index in range(dimensions):
        left = digest[(index * 2) % len(digest)]
        right = digest[(index * 2 + 1) % len(digest)]
        packed = (left << 8) | right
        values.append(packed / 65535.0)
    return values
