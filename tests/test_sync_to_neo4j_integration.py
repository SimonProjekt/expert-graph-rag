from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


@pytest.mark.integration
def test_sync_to_neo4j_compose_integration() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed in this environment")

    if not _docker_compose_available():
        pytest.skip("docker compose is not available in this environment")

    if not _integration_enabled():
        pytest.skip(
            "set RUN_NEO4J_INTEGRATION_TEST=1 to run compose-backed "
            "Neo4j integration tests"
        )

    repo_root = Path(__file__).resolve().parents[1]
    compose_project = f"expertgraphragtest_{uuid.uuid4().hex[:8]}"

    env_path = repo_root / ".env"
    created_env_file = False
    if not env_path.exists():
        shutil.copy(repo_root / ".env.example", env_path)
        created_env_file = True

    compose_base_cmd = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.integration.yml",
        "--project-name",
        compose_project,
    ]

    try:
        _run_compose(compose_base_cmd, ["up", "-d", "postgres", "neo4j"], cwd=repo_root)
        _run_compose(
            compose_base_cmd,
            ["run", "--rm", "web", "python", "manage.py", "migrate"],
            cwd=repo_root,
        )
        _run_compose(
            compose_base_cmd,
            ["run", "--rm", "web", "python", "manage.py", "ingest"],
            cwd=repo_root,
        )

        # Run twice and assert graph cardinalities remain stable.
        _run_compose(
            compose_base_cmd,
            [
                "run",
                "--rm",
                "web",
                "python",
                "manage.py",
                "sync_to_neo4j",
                "--include-collaborators",
                "--progress-every",
                "1",
            ],
            cwd=repo_root,
        )
        _run_compose(
            compose_base_cmd,
            [
                "run",
                "--rm",
                "web",
                "python",
                "manage.py",
                "sync_to_neo4j",
                "--include-collaborators",
                "--progress-every",
                "1",
            ],
            cwd=repo_root,
        )

        wrote_count = _query_single_count(
            compose_base_cmd=compose_base_cmd,
            cwd=repo_root,
            cypher="MATCH ()-[r:WROTE]->() RETURN count(r)",
        )
        has_topic_count = _query_single_count(
            compose_base_cmd=compose_base_cmd,
            cwd=repo_root,
            cypher="MATCH ()-[r:HAS_TOPIC]->() RETURN count(r)",
        )
        collaborators_count = _query_single_count(
            compose_base_cmd=compose_base_cmd,
            cwd=repo_root,
            cypher="MATCH ()-[r:COLLABORATED_WITH]->() RETURN count(r)",
        )
        paper_count = _query_single_count(
            compose_base_cmd=compose_base_cmd,
            cwd=repo_root,
            cypher="MATCH (p:Paper) RETURN count(p)",
        )

        assert paper_count == 2
        assert wrote_count == 3
        assert has_topic_count == 4
        assert collaborators_count == 1
    finally:
        _run_compose(
            compose_base_cmd,
            ["down", "-v", "--remove-orphans"],
            cwd=repo_root,
            check=False,
        )
        if created_env_file:
            env_path.unlink(missing_ok=True)


def _integration_enabled() -> bool:
    return os.environ.get("RUN_NEO4J_INTEGRATION_TEST", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _docker_compose_available() -> bool:
    process = subprocess.run(
        ["docker", "compose", "version"],
        check=False,
        capture_output=True,
        text=True,
    )
    return process.returncode == 0


def _run_compose(
    compose_base_cmd: list[str],
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        compose_base_cmd + args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and process.returncode != 0:
        raise AssertionError(
            "compose command failed\n"
            f"args={args}\n"
            f"stdout={process.stdout}\n"
            f"stderr={process.stderr}"
        )
    return process


def _query_single_count(*, compose_base_cmd: list[str], cwd: Path, cypher: str) -> int:
    process = _run_compose(
        compose_base_cmd,
        [
            "exec",
            "-T",
            "neo4j",
            "sh",
            "-lc",
            (
                "NEO4J_USER=\"${NEO4J_AUTH%%/*}\"; "
                "NEO4J_PASSWORD=\"${NEO4J_AUTH#*/}\"; "
                "cypher-shell --format plain "
                "-u \"$NEO4J_USER\" "
                "-p \"$NEO4J_PASSWORD\" "
                f"\"{cypher}\""
            ),
        ],
        cwd=cwd,
    )

    lines = [line.strip() for line in process.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"No cypher output for query: {cypher}")

    try:
        return int(lines[-1])
    except ValueError as exc:
        raise AssertionError(
            f"Unexpected cypher output for query {cypher}: {process.stdout}"
        ) from exc
