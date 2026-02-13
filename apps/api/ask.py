from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError
from django.db.models import QuerySet
from pgvector.django import CosineDistance

from apps.api.experts import ExpertRankingService
from apps.api.llm import LLMServiceError, OpenAIAnswerService
from apps.documents.embedding_backends import EmbeddingBackendError, get_embedding_backend
from apps.documents.models import Embedding, SearchAudit, SecurityLevel

logger = logging.getLogger(__name__)

_SECURITY_RANK = {
    SecurityLevel.PUBLIC: 0,
    SecurityLevel.INTERNAL: 1,
    SecurityLevel.CONFIDENTIAL: 2,
}


class AskExecutionError(Exception):
    """Raised when ask endpoint execution fails."""


class AskBackendError(AskExecutionError):
    """Raised when embedding or LLM backend fails."""


@dataclass(frozen=True)
class RetrievedChunk:
    embedding_id: int
    chunk_id: int
    paper_id: int
    paper_external_id: str
    paper_title: str
    paper_security_level: str
    text_chunk: str
    distance: float


class AskService:
    def __init__(
        self,
        *,
        top_k: int | None = None,
        max_chunk_scan: int | None = None,
        fallback_sentence_count: int | None = None,
        llm_service: OpenAIAnswerService | None = None,
    ) -> None:
        self._top_k = top_k if top_k is not None else settings.ASK_TOP_K
        self._max_chunk_scan = (
            max_chunk_scan if max_chunk_scan is not None else settings.ASK_MAX_CHUNK_SCAN
        )
        self._fallback_sentence_count = (
            fallback_sentence_count
            if fallback_sentence_count is not None
            else settings.ASK_FALLBACK_SENTENCE_COUNT
        )
        self._llm_service = llm_service

        if self._top_k <= 0:
            raise AskExecutionError("ASK_TOP_K must be greater than 0.")
        if self._max_chunk_scan <= 0:
            raise AskExecutionError("ASK_MAX_CHUNK_SCAN must be greater than 0.")
        if self._fallback_sentence_count <= 0:
            raise AskExecutionError("ASK_FALLBACK_SENTENCE_COUNT must be greater than 0.")

    def ask(
        self,
        *,
        query: str,
        clearance: str,
        endpoint: str,
        client_id: str | None,
        user_role: str | None = None,
    ) -> dict[str, object]:
        query_text = query.strip()
        if not query_text:
            raise AskExecutionError("query cannot be empty.")
        if clearance not in SecurityLevel.values:
            raise AskExecutionError(
                f"Invalid clearance: {clearance!r}. Allowed: {list(SecurityLevel.values)}"
            )

        query_vector = self._embed_query(query_text)
        retrieved = self._retrieve_top_chunks(query_vector=query_vector)

        citations, allowed_context, redacted_count = self._build_citations_and_context(
            retrieved_chunks=retrieved,
            clearance=clearance,
        )

        if settings.OPENAI_API_KEY and allowed_context:
            try:
                answer = self._generate_openai_answer(query=query_text, context=allowed_context)
            except AskBackendError:
                logger.exception("OpenAI answer generation failed, using deterministic fallback.")
                answer = self._build_extractive_answer(query=query_text, context=allowed_context)
        else:
            answer = self._build_extractive_answer(query=query_text, context=allowed_context)

        experts_payload = ExpertRankingService(top_experts=5).rank(
            query=query_text,
            clearance=clearance,
            endpoint=endpoint,
            client_id=client_id,
            user_role=(user_role or clearance),
            audit=False,
        )

        self._save_audit(
            endpoint=endpoint,
            query=query_text,
            clearance=clearance,
            user_role=(user_role or clearance),
            redacted_count=redacted_count,
            client_id=client_id,
        )

        return {
            "answer": answer,
            "citations": citations,
            "recommended_experts": experts_payload["experts"],
            "redacted_count": redacted_count,
        }

    def _embed_query(self, query: str) -> list[float]:
        try:
            backend = get_embedding_backend(
                backend_name=settings.EMBEDDING_BACKEND,
                embedding_dim=settings.EMBEDDING_DIM,
                local_model_name=settings.LOCAL_EMBEDDING_MODEL,
                openai_api_key=settings.OPENAI_API_KEY,
                openai_model_name=settings.OPENAI_EMBEDDING_MODEL,
                allow_hash_fallback=settings.ALLOW_DETERMINISTIC_EMBEDDING_FALLBACK,
            )
            vectors = backend.embed_texts([query])
        except EmbeddingBackendError as exc:
            raise AskBackendError(str(exc)) from exc

        if not vectors:
            raise AskBackendError("Embedding backend returned no vectors.")

        try:
            values = [float(value) for value in vectors[0]]
        except (TypeError, ValueError) as exc:
            raise AskBackendError("Embedding backend returned non-numeric query vector.") from exc

        expected = settings.EMBEDDING_DIM
        if len(values) == expected:
            return values
        if len(values) > expected:
            return values[:expected]
        return values + [0.0] * (expected - len(values))

    def _retrieve_top_chunks(self, *, query_vector: list[float]) -> list[RetrievedChunk]:
        queryset: QuerySet[Embedding] = (
            Embedding.objects.filter(embedding__isnull=False)
            .select_related("paper")
            .only(
                "id",
                "chunk_id",
                "text_chunk",
                "paper_id",
                "paper__external_id",
                "paper__title",
                "paper__security_level",
            )
            .annotate(distance=CosineDistance("embedding", query_vector))
            .order_by("distance", "id")
        )

        rows: list[RetrievedChunk] = []
        scanned = 0
        for embedding in queryset.iterator(chunk_size=200):
            scanned += 1
            if scanned > self._max_chunk_scan:
                break

            rows.append(
                RetrievedChunk(
                    embedding_id=embedding.id,
                    chunk_id=embedding.chunk_id,
                    paper_id=embedding.paper_id,
                    paper_external_id=embedding.paper.external_id,
                    paper_title=embedding.paper.title,
                    paper_security_level=embedding.paper.security_level,
                    text_chunk=embedding.text_chunk,
                    distance=float(embedding.distance),
                )
            )
            if len(rows) >= self._top_k:
                break

        return rows

    def _build_citations_and_context(
        self,
        *,
        retrieved_chunks: list[RetrievedChunk],
        clearance: str,
    ) -> tuple[list[dict[str, object]], list[tuple[int, RetrievedChunk]], int]:
        citations: list[dict[str, object]] = []
        allowed_context: list[tuple[int, RetrievedChunk]] = []
        redacted_count = 0

        max_clearance_rank = _SECURITY_RANK[clearance]
        for index, chunk in enumerate(retrieved_chunks, start=1):
            paper_rank = _SECURITY_RANK.get(
                chunk.paper_security_level,
                _SECURITY_RANK[SecurityLevel.CONFIDENTIAL],
            )
            if paper_rank > max_clearance_rank:
                redacted_count += 1
                citations.append(
                    {
                        "id": index,
                        "paper_title": "[REDACTED]",
                        "reference": f"redacted:{index}",
                        "chunk_id": None,
                        "redacted": True,
                    }
                )
                continue

            citations.append(
                {
                    "id": index,
                    "paper_title": chunk.paper_title,
                    "reference": chunk.paper_external_id,
                    "chunk_id": chunk.chunk_id,
                    "redacted": False,
                }
            )
            allowed_context.append((index, chunk))

        return citations, allowed_context, redacted_count

    def _generate_openai_answer(
        self,
        *,
        query: str,
        context: list[tuple[int, RetrievedChunk]],
    ) -> str:
        context_blocks = [
            f"[{citation_id}] {chunk.paper_title} ({chunk.paper_external_id}): {chunk.text_chunk}"
            for citation_id, chunk in context
        ]

        try:
            llm_service = self._llm_service or OpenAIAnswerService.from_settings()
            raw_answer = llm_service.generate_answer(query=query, context_blocks=context_blocks)
        except LLMServiceError as exc:
            logger.warning("OpenAI generation failed with code=%s.", exc.details.code)
            raise AskBackendError(exc.details.message) from exc

        return self._ensure_structured_answer(raw_answer, context=context)

    def _build_extractive_answer(
        self,
        *,
        query: str,
        context: list[tuple[int, RetrievedChunk]],
    ) -> str:
        if not context:
            return self._format_structured_answer(
                concise_answer=(
                    "Evidence is weak: no accessible chunks were found for this query at your "
                    "current clearance level."
                ),
                evidence_bullets=["No accessible evidence could be retrieved."],
                citations=[],
                follow_ups=[
                    "Can you broaden the query terms?",
                    "Can you request a higher clearance level for more evidence?",
                ],
            )

        query_terms = self._tokenize(query)
        candidates: list[tuple[float, int, int, str]] = []

        for citation_id, chunk in context:
            chunk_relevance = 1.0 / (1.0 + max(0.0, chunk.distance))
            sentences = self._split_sentences(chunk.text_chunk)
            for sentence_index, sentence in enumerate(sentences):
                sentence_terms = self._tokenize(sentence)
                overlap = len(query_terms & sentence_terms)
                score = chunk_relevance + (0.2 * overlap)
                candidates.append((score, citation_id, sentence_index, sentence.strip()))

        candidates.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))

        selected: list[str] = []
        seen_sentences: set[str] = set()
        for _score, citation_id, _sentence_index, sentence in candidates:
            if not sentence:
                continue
            if sentence in seen_sentences:
                continue
            seen_sentences.add(sentence)
            selected.append(f"{sentence} [{citation_id}]")
            if len(selected) >= self._fallback_sentence_count:
                break

        if not selected:
            citation_id, chunk = context[0]
            snippet = " ".join(chunk.text_chunk.split())[:220].rstrip()
            selected.append(f"{snippet} [{citation_id}]")

        citation_items = [
            f"[{citation_id}] {chunk.paper_external_id}" for citation_id, chunk in context
        ]
        concise = (
            "Evidence is limited; the answer is based on the highest-similarity accessible chunks."
        )

        return self._format_structured_answer(
            concise_answer=concise,
            evidence_bullets=selected,
            citations=citation_items,
            follow_ups=[
                "Which matched papers are most recent?",
                "Which recommended expert should I contact first?",
            ],
        )

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        normalized = " ".join((text or "").split())
        if not normalized:
            return []
        return [segment for segment in re.split(r"(?<=[.!?])\\s+", normalized) if segment]

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-zA-Z0-9]+", text.lower()) if len(token) >= 3}

    @staticmethod
    def _format_structured_answer(
        *,
        concise_answer: str,
        evidence_bullets: list[str],
        citations: list[str],
        follow_ups: list[str],
    ) -> str:
        evidence_lines = [f"- {line}" for line in evidence_bullets] or ["- No evidence available."]
        citation_lines = [f"- {line}" for line in citations] or ["- None"]
        follow_up_lines = [f"- {line}" for line in follow_ups] or ["- None"]
        return "\n".join(
            [
                "1. Concise answer",
                concise_answer.strip(),
                "",
                "2. Evidence bullets",
                *evidence_lines,
                "",
                "3. Citations",
                *citation_lines,
                "",
                "4. Suggested follow-up questions",
                *follow_up_lines,
            ]
        )

    def _ensure_structured_answer(
        self,
        answer: str,
        *,
        context: list[tuple[int, RetrievedChunk]],
    ) -> str:
        normalized = answer.strip()
        lowered = normalized.lower()
        required_sections = (
            "1. concise answer",
            "2. evidence bullets",
            "3. citations",
            "4. suggested follow-up questions",
        )
        if all(section in lowered for section in required_sections):
            return normalized

        citation_items = [
            f"[{citation_id}] {chunk.paper_external_id}" for citation_id, chunk in context
        ]
        return self._format_structured_answer(
            concise_answer=normalized or "Evidence is weak. No concise answer was returned.",
            evidence_bullets=[
                "The language model response was normalized into the required format."
            ],
            citations=citation_items,
            follow_ups=[
                "Can you narrow the query to a specific method or domain?",
                "Do you want only recent papers from the last 2 years?",
            ],
        )

    def _save_audit(
        self,
        *,
        endpoint: str,
        query: str,
        clearance: str,
        user_role: str,
        redacted_count: int,
        client_id: str | None,
    ) -> None:
        try:
            SearchAudit.objects.create(
                endpoint=endpoint,
                query=query,
                clearance=clearance,
                user_role=user_role,
                redacted_count=redacted_count,
                client_id=client_id,
            )
        except DatabaseError:
            logger.exception("Failed to persist SearchAudit row for ask endpoint.")
