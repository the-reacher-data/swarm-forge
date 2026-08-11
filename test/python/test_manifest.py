from __future__ import annotations

import json
from pathlib import Path
import subprocess

from swarmforge.gates import manifest


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*args], cwd=cwd, text=True, capture_output=True, check=False
    )


def init_repo(root: Path) -> None:
    run("git", "init", "-q", cwd=root)
    run("git", "config", "user.email", "test@example.com", cwd=root)
    run("git", "config", "user.name", "Test User", cwd=root)
    run("git", "branch", "-M", "main", cwd=root)
    (root / ".gitignore").write_text(".swarmforge/\n")
    (root / "README.md").write_text("initial\n")
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-q", "-m", "initial", cwd=root)


def test_manifest_is_deterministic_private_and_works_without_codegraph(
    tmp_path: Path, monkeypatch
) -> None:
    init_repo(tmp_path)
    normal = tmp_path / "src/app.py"
    normal.parent.mkdir()
    normal.write_text("VALUE = 1\n")
    sensitive = tmp_path / "config/secret-token.env"
    sensitive.parent.mkdir()
    sensitive.write_text("TOP_SECRET_PAYLOAD\n")
    monkeypatch.setenv("PRIVATE_RUNTIME_VALUE", "must-not-appear")

    first = manifest.build_manifest(
        tmp_path, gate_mode="pre-handoff", gate_result="pass"
    )
    second = manifest.build_manifest(
        tmp_path, gate_mode="pre-handoff", gate_result="pass"
    )
    first_bytes = manifest.serialize_manifest(first)
    second_bytes = manifest.serialize_manifest(second)
    document = json.loads(first_bytes)

    assert first_bytes == second_bytes
    assert list(document) == [
        "schema_version",
        "commit",
        "base",
        "diff",
        "paths",
        "gates",
        "codegraph_impact",
        "truncated",
    ]
    assert document["paths"] == ["src/app.py"]
    assert document["diff"]["files_changed"] == 2
    assert document["codegraph_impact"] is None
    assert document["gates"] == {"pre-handoff": "pass"}
    serialized = first_bytes.decode()
    assert "secret-token.env" not in serialized
    assert "TOP_SECRET_PAYLOAD" not in serialized
    assert "must-not-appear" not in serialized
    assert str(tmp_path) not in serialized


def test_manifest_reads_latest_bounded_gate_results(tmp_path: Path) -> None:
    init_repo(tmp_path)
    metrics = tmp_path / ".swarmforge/metrics/events.jsonl"
    metrics.parent.mkdir(parents=True)
    metrics.write_text(
        "ignored-prefix\n"
        + "[]\n"
        + json.dumps(
            {"event": "gate", "gate_mode": "pre-handoff", "result": "fail"}
        )
        + "\n"
        + json.dumps(
            {"event": "gate", "gate_mode": "pre-handoff", "result": "pass"}
        )
        + "\n"
        + json.dumps({"event": "task_accepted", "result": "accepted"})
        + "\n"
    )

    built = manifest.build_manifest(
        tmp_path, gate_mode="pre-complete", gate_result="fail"
    )

    assert built.document["gates"] == {
        "pre-complete": "fail",
        "pre-handoff": "pass",
    }


def test_manifest_hard_cap_drops_paths_and_stays_valid(tmp_path: Path) -> None:
    init_repo(tmp_path)
    for index in range(120):
        path = tmp_path / "generated" / f"{index:03d}-{'x' * 120}.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text("VALUE = 1\n")

    built = manifest.build_manifest(
        tmp_path,
        gate_mode="pre-handoff",
        gate_result="pass",
        max_paths=500,
    )
    payload = manifest.serialize_manifest(built)
    document = json.loads(payload)

    assert len(payload) <= 8_192
    assert document["truncated"] is True
    assert len(document["paths"]) < 120
    assert document["diff"]["files_changed"] == 120
