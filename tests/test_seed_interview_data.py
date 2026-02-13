from __future__ import annotations

from unittest.mock import call, patch

import pytest
from django.core.management import call_command
from django.test import override_settings


@pytest.mark.django_db
@override_settings(OPENALEX_API_KEY="")
def test_seed_interview_data_skips_openalex_when_key_missing() -> None:
    with patch("apps.documents.management.commands.seed_interview_data.call_command") as cmd_mock:
        call_command("seed_interview_data", skip_verify=True)

    names = [item.args[0] for item in cmd_mock.call_args_list]
    assert names[0] == "seed_demo_data"
    assert "seed_openalex" not in names
    assert "sync_to_neo4j" in names


@pytest.mark.django_db
@override_settings(OPENALEX_API_KEY="test-openalex-key")
def test_seed_interview_data_runs_openalex_queries_when_enabled() -> None:
    with patch("apps.documents.management.commands.seed_interview_data.call_command") as cmd_mock:
        call_command(
            "seed_interview_data",
            works_per_query=25,
            authors_per_query=15,
            years="2022-2026",
            backend="local",
            batch_size=64,
            skip_verify=True,
        )

    names = [item.args[0] for item in cmd_mock.call_args_list]
    assert names.count("seed_openalex") == 10
    assert names[0] == "seed_demo_data"
    assert names[-1] == "sync_to_neo4j"

    seed_calls = [item for item in cmd_mock.call_args_list if item.args[0] == "seed_openalex"]
    assert seed_calls
    first_kwargs = seed_calls[0].kwargs
    assert first_kwargs["works"] == 25
    assert first_kwargs["authors"] == 15
    assert first_kwargs["years"] == "2022-2026"
    assert first_kwargs["topics"] == ["telecom"]
    assert first_kwargs["skip_graph_sync"] is True


@pytest.mark.django_db
@override_settings(OPENALEX_API_KEY="")
def test_seed_interview_data_runs_verify_by_default() -> None:
    with patch("apps.documents.management.commands.seed_interview_data.call_command") as cmd_mock:
        call_command("seed_interview_data")

    assert call(
        "verify_data_pipeline",
        query="5G RAN optimization with AI scheduling",
    ) in cmd_mock.call_args_list
