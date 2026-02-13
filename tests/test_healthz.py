from unittest.mock import patch


def test_healthz_ok(client):
    with patch(
        "apps.health.views.HealthCheckService.check",
        return_value={
            "status": "ok",
            "checks": {
                "database": {"status": "ok", "detail": "database reachable"},
                "embeddings": {"status": "ok", "detail": "embeddings present (2)"},
                "redis": {"status": "ok", "detail": "redis reachable"},
                "neo4j": {"status": "ok", "detail": "neo4j reachable"},
            },
        },
    ):
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_healthz_degraded(client):
    with patch(
        "apps.health.views.HealthCheckService.check",
        return_value={
            "status": "degraded",
            "checks": {
                "database": {"status": "ok", "detail": "database reachable"},
                "neo4j": {"status": "ok", "detail": "neo4j reachable"},
                "embeddings": {"status": "error", "detail": "no embeddings present"},
                "redis": {"status": "ok", "detail": "redis reachable"},
            },
        },
    ):
        response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
