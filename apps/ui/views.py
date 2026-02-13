from __future__ import annotations

from collections import Counter
from io import StringIO
from typing import Any

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.common.demo_auth import (
    clear_session_identity,
    get_session_name,
    get_session_role,
    normalize_role,
    set_session_identity,
)
from apps.documents.models import Author, Paper, SecurityLevel
from apps.documents.verification import DataPipelineVerifier

TAB_PAPERS = "papers"
TAB_EXPERTS = "experts"
TAB_GRAPH = "graph"
TAB_ASK = "ask"
VALID_TABS = {TAB_PAPERS, TAB_EXPERTS, TAB_GRAPH, TAB_ASK}
DEFAULT_EXAMPLE_QUERIES = (
    "5G RAN optimization with AI scheduling",
    "network slicing orchestration reliability",
    "O-RAN xApp policy optimization",
    "federated learning for telecom networks",
    "core network anomaly detection",
    "energy efficient base station control",
)
LANDING_TUTORIAL_STEPS = (
    "Select a telecom-focused query from the examples.",
    "Start in Papers to inspect relevance and snippets.",
    "Move to Experts to find ranked researchers and institutions.",
    "Open Graph to explain relationships between authors, papers, and topics.",
    "Use Ask for a grounded answer with citations.",
)
LANDING_CAPABILITIES = (
    {
        "title": "Access-Aware Retrieval",
        "description": (
            "All results are filtered by clearance (PUBLIC/INTERNAL/CONFIDENTIAL) before "
            "response payloads are built."
        ),
    },
    {
        "title": "Expert Ranking",
        "description": (
            "Experts are scored by semantic relevance, topic coverage, recency, and optional "
            "graph centrality."
        ),
    },
    {
        "title": "Graph-Backed Explainability",
        "description": (
            "The graph tab surfaces Author-Paper-Topic paths so rankings are explainable to "
            "recruiters and technical leads."
        ),
    },
    {
        "title": "Grounded Ask",
        "description": (
            "Ask returns evidence-backed answers with citations and recommended experts from "
            "retrieved context."
        ),
    },
)


@require_http_methods(["GET", "POST"])
def demo_login(request: HttpRequest) -> HttpResponse:
    next_url = request.GET.get("next") or request.POST.get("next") or "/"
    error: str | None = None

    if request.method == "POST":
        role = normalize_role(request.POST.get("role"))
        name = (request.POST.get("name") or "").strip()
        set_session_identity(request, role=role, name=name or None)
        return redirect(next_url)

    context = {
        "next": next_url,
        "role_options": list(SecurityLevel.values),
        "current_role": get_session_role(request),
        "current_name": get_session_name(request) or "",
        "error": error,
    }
    return render(request, "ui/demo_login.html", context)


@require_GET
def landing(request: HttpRequest) -> HttpResponse:
    session_role = get_session_role(request)
    session_name = get_session_name(request)

    featured_works = list(
        Paper.objects.filter(security_level=SecurityLevel.PUBLIC)
        .order_by("-published_date", "-id")
        .values("title", "published_date")[:8]
    )
    featured_experts = list(
        Author.objects.filter(authorships__paper__security_level=SecurityLevel.PUBLIC)
        .annotate(paper_count=Count("authorships__paper", distinct=True))
        .order_by("-paper_count", "name")
        .values("id", "name", "institution_name", "paper_count")[:8]
    )

    context: dict[str, Any] = {
        "session_role": session_role,
        "session_name": session_name,
        "llm_enabled": bool(settings.OPENAI_API_KEY),
        "openalex_live_fetch_enabled": bool(
            settings.OPENALEX_LIVE_FETCH and bool(settings.OPENALEX_API_KEY)
        ),
        "example_queries": list(DEFAULT_EXAMPLE_QUERIES),
        "tutorial_steps": list(LANDING_TUTORIAL_STEPS),
        "capabilities": list(LANDING_CAPABILITIES),
        "featured_works": featured_works,
        "featured_experts": featured_experts,
    }
    return render(request, "ui/landing.html", context)


@require_http_methods(["POST"])
def demo_logout(request: HttpRequest) -> HttpResponse:
    next_url = request.POST.get("next") or "/demo/"
    clear_session_identity(request)
    return redirect(next_url)


@require_GET
def home(request):
    query = (request.GET.get("query") or "").strip()
    session_role = get_session_role(request)
    active_tab = (request.GET.get("tab") or TAB_PAPERS).strip().lower()
    if active_tab not in VALID_TABS:
        active_tab = TAB_PAPERS

    clearance = _normalize_clearance(request.GET.get("clearance"), default=session_role)
    session_name = get_session_name(request)
    ask_query = (request.GET.get("ask_query") or query).strip()
    sort_order = (request.GET.get("sort") or "relevance").strip().lower()
    if sort_order not in {"relevance", "recency"}:
        sort_order = "relevance"
    allowed_levels = _allowed_levels(clearance)

    recent_works = list(
        Paper.objects.filter(security_level__in=allowed_levels)
        .order_by("-published_date", "-id")
        .values("title", "published_date")[:6]
    )
    top_experts = list(
        Author.objects.filter(authorships__paper__security_level__in=allowed_levels)
        .annotate(paper_count=Count("authorships__paper", distinct=True))
        .order_by("-paper_count", "name")
        .values("id", "name", "institution_name", "paper_count")[:6]
    )

    context: dict[str, Any] = {
        "query": query,
        "ask_query": ask_query,
        "clearance": clearance,
        "sort_order": sort_order,
        "session_role": session_role,
        "session_name": session_name,
        "clearance_options": list(SecurityLevel.values),
        "active_tab": active_tab,
        "llm_enabled": bool(settings.OPENAI_API_KEY),
        "openalex_live_fetch_enabled": bool(
            settings.OPENALEX_LIVE_FETCH and bool(settings.OPENALEX_API_KEY)
        ),
        "example_queries": list(DEFAULT_EXAMPLE_QUERIES),
        "recent_works": recent_works,
        "top_experts": top_experts,
        "api_search_url": "/api/search",
        "api_experts_url": "/api/experts",
        "api_ask_url": "/api/ask",
        "ui_bootstrap": {
            "initialQuery": query,
            "initialAskQuery": ask_query,
            "initialClearance": clearance,
            "initialSort": sort_order,
            "initialTab": active_tab,
            "apiSearchUrl": "/api/search",
            "apiExpertsUrl": "/api/experts",
            "apiAskUrl": "/api/ask",
            "expertProfileBasePath": "/experts/",
            "openAlexLiveFetchEnabled": bool(
                settings.OPENALEX_LIVE_FETCH and bool(settings.OPENALEX_API_KEY)
            ),
        },
    }
    return render(request, "ui/home.html", context)


@staff_member_required
@require_http_methods(["GET", "POST"])
def debug_data(request: HttpRequest) -> HttpResponse:
    verifier = DataPipelineVerifier()
    snapshot = verifier.collect_snapshot()

    verification_output: str | None = None
    verification_passed: bool | None = None
    sample_query = (request.POST.get("query") or "").strip()

    if request.method == "POST":
        stream = StringIO()
        kwargs: dict[str, Any] = {"stdout": stream, "stderr": stream}
        if sample_query:
            kwargs["query"] = sample_query

        try:
            call_command("verify_data_pipeline", **kwargs)
            verification_passed = True
        except CommandError:
            verification_passed = False
        except Exception as exc:  # noqa: BLE001
            verification_passed = False
            stream.write(f"\nUnexpected verification failure: {exc}\n")

        verification_output = stream.getvalue()

    context = {
        "snapshot": snapshot,
        "verification_output": verification_output,
        "verification_passed": verification_passed,
        "sample_query": sample_query,
    }
    return render(request, "ui/debug_data.html", context)


@require_GET
def expert_profile(request, author_id: int):
    author = get_object_or_404(Author, pk=author_id)
    session_role = get_session_role(request)
    session_name = get_session_name(request)
    clearance = _normalize_clearance(request.GET.get("clearance"), default=session_role)
    query = (request.GET.get("query") or "").strip()

    allowed_levels = _allowed_levels(clearance)
    papers = list(
        Paper.objects.filter(authorships__author=author, security_level__in=allowed_levels)
        .distinct()
        .prefetch_related("paper_topics__topic", "authorships__author")
        .order_by("-published_date", "id")[:10]
    )

    topic_counter: Counter[str] = Counter()
    collaborator_counter: Counter[int] = Counter()
    collaborator_by_id: dict[int, Author] = {}

    for paper in papers:
        for paper_topic in paper.paper_topics.all():
            topic_counter.update([paper_topic.topic.name])

        for authorship in paper.authorships.all():
            collaborator = authorship.author
            if collaborator.id == author.id:
                continue
            collaborator_counter[collaborator.id] += 1
            collaborator_by_id[collaborator.id] = collaborator

    top_topics = [name for name, _count in topic_counter.most_common(8)]
    collaborators: list[dict[str, Any]] = []
    for collaborator_id, paper_count in collaborator_counter.most_common(10):
        collaborator = collaborator_by_id[collaborator_id]
        collaborators.append(
            {
                "id": collaborator.id,
                "name": collaborator.name,
                "institution": collaborator.institution_name,
                "paper_count": paper_count,
            }
        )

    context = {
        "author": author,
        "clearance": clearance,
        "session_role": session_role,
        "session_name": session_name,
        "query": query,
        "top_topics": top_topics,
        "top_papers": papers,
        "collaborators": collaborators,
    }
    return render(request, "ui/expert_profile.html", context)


def _normalize_clearance(raw_value: str | None, *, default: str) -> str:
    value = (raw_value or default).strip().upper()
    if value not in SecurityLevel.values:
        return default
    return value


def _allowed_levels(clearance: str) -> tuple[str, ...]:
    if clearance == SecurityLevel.PUBLIC:
        return (SecurityLevel.PUBLIC,)
    if clearance == SecurityLevel.INTERNAL:
        return (SecurityLevel.PUBLIC, SecurityLevel.INTERNAL)
    return (SecurityLevel.PUBLIC, SecurityLevel.INTERNAL, SecurityLevel.CONFIDENTIAL)
