from __future__ import annotations

import pytest

from apps.documents.models import Author


@pytest.mark.django_db
def test_ui_routes_smoke(client) -> None:
    author = Author.objects.create(
        name="Smoke Test Author",
        external_id="author:ui:smoke:001",
        institution_name="Demo Institute",
    )

    landing_response = client.get("/")
    home_response = client.get("/demo/")
    login_response = client.get("/demo/login/")
    profile_response = client.get(f"/experts/{author.id}/")

    assert landing_response.status_code == 200
    assert home_response.status_code == 200
    assert login_response.status_code == 200
    assert profile_response.status_code == 200

    landing_content = landing_response.content.decode("utf-8")
    home_content = home_response.content.decode("utf-8")
    assert "What this system does" in landing_content
    assert "Open Workspace" in landing_content
    assert "Set Demo Role" not in landing_content
    assert "LLM Enabled" not in landing_content
    assert "SignalGraph AI" in home_content
    assert "Try These Queries" in home_content
    assert "Query expansion depth" in home_content
    assert "Path-only focus mode" in home_content
    assert "Change demo role" not in home_content
