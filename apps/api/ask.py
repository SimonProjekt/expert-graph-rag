from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError
from django.db.models import QuerySet
from pgvector.django import CosineDistance

from apps.api.experts import ExpertRankingService
from apps.api.llm import LLMServiceError, OpenAIAnswerService
from apps.api.query_optimizer import optimize_query
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

        optimized = optimize_query(query_text)
        retrieval_query = optimized.optimized_query or optimized.normalized_query or query_text
        query_vector = self._embed_query(retrieval_query)
        retrieved = self._retrieve_top_chunks(query_vector=query_vector)

        citations, allowed_context, redacted_count = self._build_citations_and_context(
            retrieved_chunks=retrieved,
            clearance=clearance,
        )

        if settings.OPENAI_API_KEY and allowed_context:
            try:
                answer_payload = self._generate_openai_answer(
                    query=query_text,
                    context=allowed_context,
                )
            except AskBackendError:
                logger.exception("OpenAI answer generation failed, using deterministic fallback.")
                answer_payload = self._build_extractive_answer(
                    query=query_text,
                    context=allowed_context,
                )
        else:
            answer_payload = self._build_extractive_answer(query=query_text, context=allowed_context)

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
            "answer": answer_payload["answer"],
            "answer_payload": answer_payload,
            "optimized_query": retrieval_query,
            "citations": citations,
            "recommended_experts": experts_payload["experts"],
            "redacted_count": redacted_count,
        }

    def _embed_query(self, query: str) -> list[float]:
        primary_backend = settings.EMBEDDING_BACKEND
        try:
            return self._embed_query_with_backend(query=query, backend_name=primary_backend)
        except EmbeddingBackendError as exc:
            if not self._should_try_local_fallback(primary_backend):
                raise AskBackendError(str(exc)) from exc

            logger.warning(
                "Primary embedding backend failed for ask; retrying with local fallback."
            )
            try:
                return self._embed_query_with_backend(query=query, backend_name="local")
            except EmbeddingBackendError as fallback_exc:
                raise AskBackendError(
                    f"{exc} (local fallback failed: {fallback_exc})"
                ) from fallback_exc

    def _embed_query_with_backend(self, *, query: str, backend_name: str) -> list[float]:
        backend = get_embedding_backend(
            backend_name=backend_name,
            embedding_dim=settings.EMBEDDING_DIM,
            local_model_name=settings.LOCAL_EMBEDDING_MODEL,
            openai_api_key=settings.OPENAI_API_KEY,
            openai_model_name=settings.OPENAI_EMBEDDING_MODEL,
            allow_hash_fallback=settings.ALLOW_DETERMINISTIC_EMBEDDING_FALLBACK,
        )
        vectors = backend.embed_texts([query])
        if not vectors:
            raise EmbeddingBackendError("Embedding backend returned no vectors.")

        try:
            values = [float(value) for value in vectors[0]]
        except (TypeError, ValueError) as exc:
            raise EmbeddingBackendError(
                "Embedding backend returned non-numeric query vector."
            ) from exc

        expected = settings.EMBEDDING_DIM
        if len(values) == expected:
            return values
        if len(values) > expected:
            return values[:expected]
        return values + [0.0] * (expected - len(values))

    @staticmethod
    def _should_try_local_fallback(backend_name: str) -> bool:
        if not settings.ALLOW_DETERMINISTIC_EMBEDDING_FALLBACK:
            return False

        normalized = (backend_name or "auto").strip().lower()
        if normalized == "local":
            return False
        if normalized == "openai":
            return True
        return bool(settings.OPENAI_API_KEY)

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
    ) -> dict[str, object]:
        context_records = [
            {
                "citation_id": citation_id,
                "source": chunk.paper_external_id,
                "paper_title": chunk.paper_title,
                "chunk_text": chunk.text_chunk,
            }
            for citation_id, chunk in context
        ]
        context_blocks = [json.dumps(record, ensure_ascii=True) for record in context_records]

        try:
            llm_service = self._llm_service or OpenAIAnswerService.from_settings()
            raw_answer = llm_service.generate_answer(query=query, context_blocks=context_blocks)
        except LLMServiceError as exc:
            logger.warning("OpenAI generation failed with code=%s.", exc.details.code)
            raise AskBackendError(exc.details.message) from exc

        parsed = self._parse_llm_json(raw_answer)
        if parsed is None:
            try:
                corrected = llm_service.generate_answer(
                    query=query,
                    context_blocks=context_blocks,
                    correction_prompt=(
                        "Your previous answer was invalid JSON. Return ONLY valid JSON with keys: "
                        "answer, key_points, evidence_used, confidence, limitations."
                    ),
                )
            except LLMServiceError as exc:
                logger.warning("OpenAI correction request failed with code=%s.", exc.details.code)
                raise AskBackendError(exc.details.message) from exc
            parsed = self._parse_llm_json(corrected)

        if parsed is None:
            raise AskBackendError("LLM did not return valid JSON output.")

        return self._normalize_answer_payload(parsed, context=context)

    def _build_extractive_answer(
        self,
        *,
        query: str,
        context: list[tuple[int, RetrievedChunk]],
    ) -> dict[str, object]:
        if not context:
            return {
                "answer": (
                    "Evidence is weak: no accessible chunks were found for this query at your "
                    "current clearance level."
                ),
                "key_points": ["No accessible evidence could be retrieved."],
                "evidence_used": [],
                "confidence": "low",
                "limitations": (
                    "No accessible chunks were available, so the summary could not reference "
                    "specific source passages."
                ),
            }

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

        primary_sentence = re.sub(r"\s*\[\d+\]\s*$", "", selected[0]).strip()
        if primary_sentence:
            concise = (
                f"{primary_sentence}. This summary is grounded in the highest-similarity retrieved "
                "chunks."
            )
        else:
            concise = "The answer is grounded in the highest-similarity retrieved chunks."
        if len(context) <= 1:
            concise = f"Evidence is limited. {concise}"

        evidence_used = []
        for citation_id, chunk in context:
            evidence_used.append(
                {
                    "source": f"[{citation_id}] {chunk.paper_external_id}",
                    "reason": "Top similarity chunk selected for extractive grounding.",
                }
            )

        return {
            "answer": concise,
            "key_points": selected,
            "evidence_used": evidence_used,
            "confidence": "medium" if len(context) >= 2 else "low",
            "limitations": (
                "Deterministic extractive mode was used, so output quality is bounded by "
                "retrieved chunk coverage."
            ),
        }

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
    def _parse_llm_json(raw_answer: str) -> dict[str, object] | None:
        candidate = (raw_answer or "").strip()
        if not candidate:
            return None

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
            if not match:
                return None
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None

        if not isinstance(parsed, dict):
            return None
        return parsed

    @staticmethod
    def _normalize_answer_payload(
        payload: dict[str, object],
        *,
        context: list[tuple[int, RetrievedChunk]],
    ) -> dict[str, object]:
        answer_value = payload.get("answer")
        answer = (
            str(answer_value).strip()
            if isinstance(answer_value, str) and answer_value.strip()
            else "Evidence is weak. No concise answer was returned."
        )

        raw_key_points = payload.get("key_points")
        key_points = (
            [str(item).strip() for item in raw_key_points if str(item).strip()]
            if isinstance(raw_key_points, list)
            else []
        )
        if not key_points:
            key_points = ["No key points were returned."]

        raw_evidence = payload.get("evidence_used")
        evidence_used: list[dict[str, str]] = []
        if isinstance(raw_evidence, list):
            for item in raw_evidence:
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source", "")).strip()
                reason = str(item.get("reason", "")).strip()
                if not reason:
                    continue
                evidence_used.append(
                    {
                        "source": source or "unknown source",
                        "reason": reason,
                    }
                )
        if not evidence_used:
            evidence_used = [
                {
                    "source": f"[{citation_id}] {chunk.paper_external_id}",
                    "reason": "Retrieved as accessible supporting evidence.",
                }
                for citation_id, chunk in context[:2]
            ]

        confidence_raw = str(payload.get("confidence", "")).strip().lower()
        confidence = confidence_raw if confidence_raw in {"high", "medium", "low"} else "medium"

        limitations_value = payload.get("limitations")
        limitations = (
            str(limitations_value).strip()
            if isinstance(limitations_value, str) and limitations_value.strip()
            else "Response quality depends on the retrieved chunk coverage and ranking quality."
        )

        return {
            "answer": answer,
            "key_points": key_points,
            "evidence_used": evidence_used,
            "confidence": confidence,
            "limitations": limitations,
        }

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
