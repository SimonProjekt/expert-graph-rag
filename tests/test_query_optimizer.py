from __future__ import annotations

from apps.api.query_optimizer import optimize_query


def test_query_optimizer_normalizes_and_expands_domain_terms() -> None:
    optimized = optimize_query("How can we improve 5G RAN optimization with AI scheduling?")

    assert optimized.original_query.startswith("How can we")
    assert "how" not in optimized.base_terms
    assert "5g" in optimized.base_terms
    assert "ran" in optimized.expanded_terms
    assert "scheduling" in optimized.expanded_terms
    assert optimized.optimized_query
    assert optimized.domain_terms


def test_query_optimizer_removes_noise_tokens() -> None:
    optimized = optimize_query("please demo show answer about telecom networks")

    assert "please" not in optimized.base_terms
    assert "demo" not in optimized.base_terms
    assert "answer" not in optimized.base_terms
    assert "telecom" in optimized.base_terms
