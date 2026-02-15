from __future__ import annotations

import re
from dataclasses import dataclass

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "this",
    "to",
    "using",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
}

_NOISE_TOKENS = {
    "demo",
    "please",
    "thanks",
    "thank",
    "question",
    "answer",
    "show",
    "tell",
    "about",
}

_DOMAIN_KEYWORDS = {
    "5g",
    "6g",
    "ran",
    "oran",
    "ric",
    "xapp",
    "xapps",
    "network",
    "networks",
    "slicing",
    "slice",
    "orchestration",
    "telecom",
    "telecommunications",
    "wireless",
    "radio",
    "edge",
    "mimo",
}

_DOMAIN_SYNONYMS = {
    "5g": ("ran", "radio", "wireless"),
    "6g": ("ran", "radio", "wireless"),
    "ran": ("radio", "ric", "xapp"),
    "oran": ("o", "ran", "ric", "xapp"),
    "ric": ("xapp", "orchestration"),
    "xapp": ("xapps", "ric"),
    "network": ("telecom", "wireless"),
    "networks": ("telecom", "wireless"),
    "slicing": ("slice", "orchestration"),
    "slice": ("slicing", "orchestration"),
    "orchestration": ("automation", "scheduling"),
    "federated": ("distributed", "learning"),
    "optimization": ("optimisation", "scheduling"),
    "anomaly": ("detection", "monitoring"),
    "telecom": ("telecommunications", "network"),
    "radio": ("ran", "wireless"),
}


@dataclass(frozen=True)
class OptimizedQuery:
    original_query: str
    normalized_query: str
    optimized_query: str
    base_terms: tuple[str, ...]
    expanded_terms: tuple[str, ...]
    domain_terms: tuple[str, ...]


def optimize_query(query: str) -> OptimizedQuery:
    original_query = (query or "").strip()
    lowered = original_query.lower()
    raw_tokens = _tokenize(lowered)

    base_terms = [
        token
        for token in raw_tokens
        if _is_content_token(token) and token not in _STOPWORDS and token not in _NOISE_TOKENS
    ]
    if not base_terms:
        base_terms = [token for token in raw_tokens if _is_content_token(token)]

    deduped_base_terms = _dedupe_preserve_order(base_terms)
    detected_domain_terms = [term for term in deduped_base_terms if term in _DOMAIN_KEYWORDS]

    expanded_terms = list(deduped_base_terms)
    for term in deduped_base_terms:
        for synonym in _DOMAIN_SYNONYMS.get(term, ()):
            if _is_content_token(synonym):
                expanded_terms.append(synonym)

    if detected_domain_terms:
        for domain_term in detected_domain_terms:
            for synonym in _DOMAIN_SYNONYMS.get(domain_term, ()):
                if _is_content_token(synonym):
                    expanded_terms.append(synonym)

    deduped_expanded_terms = _dedupe_preserve_order(expanded_terms)
    normalized_query = " ".join(deduped_base_terms)
    optimized_query = " ".join(deduped_expanded_terms)

    return OptimizedQuery(
        original_query=original_query,
        normalized_query=normalized_query,
        optimized_query=optimized_query,
        base_terms=tuple(deduped_base_terms),
        expanded_terms=tuple(deduped_expanded_terms),
        domain_terms=tuple(_dedupe_preserve_order(detected_domain_terms)),
    )


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text) if token]


def _is_content_token(token: str) -> bool:
    if len(token) < 2:
        return False
    if token.isdigit():
        return False
    return True


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
