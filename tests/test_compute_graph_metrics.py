from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.documents.models import Author
from apps.graphsync.services import (
    GraphMetricsGDSUnavailable,
    GraphMetricsService,
)


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


class FakeDriver:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False

    def verify_connectivity(self) -> None:
        return None

    def session(self):
        return FakeSession()


@pytest.mark.django_db
def test_graph_metrics_service_falls_back_when_gds_missing() -> None:
    primary = Author.objects.create(
        name="Primary",
        external_id="author:metrics:primary:001",
        institution_name="Lab A",
    )
    secondary = Author.objects.create(
        name="Secondary",
        external_id="author:metrics:secondary:001",
        institution_name="Lab B",
    )

    service = GraphMetricsService()
    with patch("apps.graphsync.services.GraphDatabase.driver", return_value=FakeDriver()):
        with patch.object(
            GraphMetricsService,
            "_compute_gds_pagerank",
            side_effect=GraphMetricsGDSUnavailable("missing"),
        ) as gds_mock:
            with patch.object(
                GraphMetricsService,
                "_compute_degree_centrality",
                return_value={primary.external_id: 7.0},
            ) as degree_mock:
                result = service.compute_author_centrality(reset_missing=True)

    primary.refresh_from_db()
    secondary.refresh_from_db()

    assert result.method == "degree_centrality"
    assert result.authors_total == 2
    assert result.authors_updated == 2
    assert primary.centrality_score == 7.0
    assert secondary.centrality_score == 0.0
    gds_mock.assert_called_once()
    degree_mock.assert_called_once()


@pytest.mark.django_db
def test_compute_graph_metrics_command_runs_without_gds() -> None:
    author = Author.objects.create(
        name="Cmd Author",
        external_id="author:metrics:cmd:001",
        institution_name="Ops Lab",
    )

    output = StringIO()
    with patch("apps.graphsync.services.GraphDatabase.driver", return_value=FakeDriver()):
        with patch.object(
            GraphMetricsService,
            "_compute_gds_pagerank",
            side_effect=GraphMetricsGDSUnavailable("missing"),
        ):
            with patch.object(
                GraphMetricsService,
                "_compute_degree_centrality",
                return_value={author.external_id: 3.5},
            ):
                call_command("compute_graph_metrics", stdout=output)

    author.refresh_from_db()

    assert author.centrality_score == 3.5
    assert "method=degree_centrality" in output.getvalue()
