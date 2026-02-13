from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.documents.embedding_backends import (
    EmbeddingBackendError,
    LocalSentenceTransformerBackend,
)


def test_local_backend_uses_hash_fallback_when_model_unavailable() -> None:
    backend = LocalSentenceTransformerBackend(
        model_name="missing-model",
        embedding_dim=8,
        allow_hash_fallback=True,
    )

    with patch.object(
        LocalSentenceTransformerBackend,
        "_load_model",
        side_effect=EmbeddingBackendError("model unavailable"),
    ):
        vectors = backend.embed_texts(["alpha", "beta"])

    assert len(vectors) == 2
    assert len(vectors[0]) == 8
    assert vectors[0] != vectors[1]


def test_local_backend_raises_when_hash_fallback_disabled() -> None:
    backend = LocalSentenceTransformerBackend(
        model_name="missing-model",
        embedding_dim=8,
        allow_hash_fallback=False,
    )

    with patch.object(
        LocalSentenceTransformerBackend,
        "_load_model",
        side_effect=EmbeddingBackendError("model unavailable"),
    ):
        with pytest.raises(EmbeddingBackendError):
            backend.embed_texts(["alpha"])
