from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).parents[2]
METRICS = REPO_ROOT / "swarmforge" / "telemetry" / "metrics.py"


def load_metrics():
    spec = importlib.util.spec_from_file_location("swarmforge_metrics", METRICS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def events(root: Path) -> list[dict[str, object]]:
    path = root / ".swarmforge" / "metrics" / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_record_writes_ordered_schema_and_resolves_backend(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / ".swarmforge").mkdir()
    (tmp_path / ".swarmforge" / "roles.tsv").write_text(
        f"coder\tcoder\t{tmp_path}\tsession\tCoder\tcodex-primary\ttask\teager\n"
    )
    monkeypatch.setenv("SWARMFORGE_ROLE", "coder")
    metrics = load_metrics()

    assert metrics.record_event(
        tmp_path,
        event="gate",
        id="gate-id",
        result="pass",
        duration_ms=12,
        gate_failure_count=0,
        tool_output_bytes_exposed=0,
        gate_mode="fast",
    )

    raw = (tmp_path / ".swarmforge/metrics/events.jsonl").read_text().strip()
    event = json.loads(raw)
    assert list(event) == [
        "schema_version",
        "ts",
        "event",
        "id",
        "role",
        "backend_instance",
        "result",
        "duration_ms",
        "handoff_count",
        "gate_failure_count",
        "tool_output_bytes_exposed",
        "tokens",
        "gate_mode",
        "test_selection",
        "test_selection_reason",
        "affected_test_count",
        "route",
        "risk_score",
    ]
    assert event == {
        "schema_version": 1,
        "ts": event["ts"],
        "event": "gate",
        "id": "gate-id",
        "role": "coder",
        "backend_instance": "codex-primary",
        "result": "pass",
        "duration_ms": 12,
        "handoff_count": None,
        "gate_failure_count": 0,
        "tool_output_bytes_exposed": 0,
        "tokens": {"input": None, "output": None, "cache_read": None},
        "gate_mode": "fast",
        "test_selection": None,
        "test_selection_reason": None,
        "affected_test_count": None,
        "route": None,
        "risk_score": None,
    }


def test_gate_route_fields_are_bounded_enums_and_counts(tmp_path: Path) -> None:
    metrics = load_metrics()

    assert metrics.record_event(
        tmp_path,
        event="gate",
        result="pass",
        gate_failure_count=0,
        route="architect",
        risk_score=7,
    )
    assert events(tmp_path)[0]["route"] == "architect"
    assert events(tmp_path)[0]["risk_score"] == 7
    assert not metrics.record_event(
        tmp_path,
        event="gate",
        result="pass",
        gate_failure_count=0,
        route="arbitrary-reviewer",
        risk_score=7,
    )
    assert not metrics.record_event(
        tmp_path,
        event="task_accepted",
        result="accepted",
        route="done",
        risk_score=0,
    )


def test_disabled_and_invalid_configuration_do_not_create_metrics(
    tmp_path: Path, monkeypatch
) -> None:
    metrics = load_metrics()
    monkeypatch.setenv("SWARMFORGE_TELEMETRY", "0")
    assert not metrics.record_event(
        tmp_path, event="task_accepted", id="task", result="accepted"
    )
    assert not (tmp_path / ".swarmforge/metrics").exists()

    monkeypatch.delenv("SWARMFORGE_TELEMETRY")
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/python-gates.toml").write_text(
        "[telemetry]\nenabled = 'yes'\n"
    )
    assert not metrics.record_event(
        tmp_path, event="task_accepted", id="task", result="accepted"
    )
    assert not (tmp_path / ".swarmforge/metrics").exists()


def test_escaping_telemetry_dir_disables_recording(tmp_path: Path) -> None:
    metrics = load_metrics()
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    (root / "swarmforge").mkdir(parents=True)
    for directory in (str(outside), "../outside", "sub/../../outside"):
        (root / "swarmforge/python-gates.toml").write_text(
            f'[telemetry]\ndir = "{directory}"\n'
        )
        assert not metrics.record_event(
            root, event="task_accepted", id="task", result="accepted"
        )
    assert not outside.exists()
    assert not (root / ".swarmforge/metrics").exists()


def test_valid_relative_dir_still_records(tmp_path: Path) -> None:
    metrics = load_metrics()
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/python-gates.toml").write_text(
        "[telemetry]\ndir = 'metrics/sub'\n"
    )
    assert metrics.record_event(
        tmp_path, event="task_accepted", id="task", result="accepted"
    )
    line = (tmp_path / "metrics/sub/events.jsonl").read_text().strip()
    assert json.loads(line)["id"] == "task"


def test_cli_caps_strings_and_concurrent_appends_are_complete(tmp_path: Path) -> None:
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                str(METRICS),
                "record",
                "--root",
                str(tmp_path),
                "--event",
                "task_accepted",
                "--id",
                f"task-{index}" + ("x" * 400),
                "--result",
                "accepted",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(8)
    ]

    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0
        assert stdout == stderr == ""

    recorded = events(tmp_path)
    assert len(recorded) == 8
    assert all(len(str(event["id"])) == 256 for event in recorded)
    assert all(event["result"] == "accepted" for event in recorded)


def test_unwritable_metrics_target_is_silent(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    (tmp_path / "swarmforge").mkdir()
    (tmp_path / "swarmforge/python-gates.toml").write_text(
        "[telemetry]\ndir = 'blocked/metrics'\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(METRICS),
            "record",
            "--root",
            str(tmp_path),
            "--event",
            "task_completed",
            "--id",
            "task",
            "--result",
            "completed",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
