from pathlib import Path

import pytest
import yaml

from agent_system.evaluations import (
    CORE_AGENT_NAMES,
    EvaluationCase,
    compare_native_variants,
    evaluate_static_contract,
    load_cases,
)


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_every_core_agent_has_positive_and_negative_cases() -> None:
    cases = load_cases(Path("agents/evals/core-agents.yaml"))
    for target in CORE_AGENT_NAMES:
        selected = [case for case in cases if case.target == target]
        assert sum(case.expected == "invoke" for case in selected) >= 2
        assert sum(case.expected == "do-not-invoke" for case in selected) >= 2


def test_skill_contract_covers_each_forward_testing_phase() -> None:
    cases = load_cases(Path("agents/evals/skill-contract.yaml"))
    assert {case.kind for case in cases} == {
        "application",
        "baseline",
        "discovery",
        "negative",
        "variation",
    }


@pytest.mark.parametrize(
    "document",
    [
        {"version": 1, "cases": [], "extra": True},
        {
            "version": 1,
            "cases": [
                {
                    "id": "case-1",
                    "target": "verifier",
                    "kind": "trigger",
                    "prompt": "Verify the acceptance evidence.",
                    "expected": "invoke",
                    "forbidden": [],
                    "extra": True,
                }
            ],
        },
    ],
)
def test_load_cases_rejects_unknown_schema_fields(
    tmp_path: Path, document: object
) -> None:
    path = tmp_path / "cases.yaml"
    _write_yaml(path, document)

    with pytest.raises(ValueError, match="unknown fields"):
        load_cases(path)


def test_load_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    case = {
        "id": "duplicate",
        "target": "verifier",
        "kind": "trigger",
        "prompt": "Verify the evidence.",
        "expected": "invoke",
        "forbidden": [],
    }
    path = tmp_path / "cases.yaml"
    _write_yaml(path, {"version": 1, "cases": [case, case]})

    with pytest.raises(ValueError, match="duplicate case id"):
        load_cases(path)


def test_static_contract_reports_forbidden_ownership_claim(tmp_path: Path) -> None:
    artifact = tmp_path / "verifier.toml"
    artifact.write_text(
        'name = "verifier"\n'
        'description = "Verify independent evidence"\n'
        'developer_instructions = "Implement the requested changes."\n',
        encoding="utf-8",
    )
    case = EvaluationCase(
        id="verifier-boundary",
        target="verifier",
        kind="contract",
        prompt="Verify without implementation.",
        expected="present",
        forbidden=("implement the requested changes",),
    )

    assert [finding.code for finding in evaluate_static_contract(case, artifact)] == [
        "forbidden-contract-text"
    ]


def test_static_contract_reports_target_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "implementer.toml"
    artifact.write_text(
        'name = "implementer"\n'
        'description = "Implement one bounded task"\n'
        'developer_instructions = "Make the approved change."\n',
        encoding="utf-8",
    )
    case = EvaluationCase(
        id="wrong-target",
        target="verifier",
        kind="trigger",
        prompt="Verify the evidence.",
        expected="invoke",
        forbidden=(),
    )

    assert [finding.code for finding in evaluate_static_contract(case, artifact)] == [
        "evaluation-target-mismatch"
    ]


def test_equivalence_mapping_covers_all_core_roles() -> None:
    document = yaml.safe_load(Path("agents/evals/equivalence.yaml").read_text())
    assert {mapping["role"] for mapping in document["mappings"]} == set(
        CORE_AGENT_NAMES
    )


def test_native_variants_share_declared_invariants(tmp_path: Path) -> None:
    claude_dir = tmp_path / "claude"
    codex_dir = tmp_path / "codex"
    claude_dir.mkdir()
    codex_dir.mkdir()
    (claude_dir / "verifier.md").write_text(
        "---\n"
        "name: verifier\n"
        "description: Verify evidence independently.\n"
        "tools: [Read, Grep, Glob]\n"
        "---\n"
        "Check acceptance criteria and report verdict, evidence, and unverified items.\n",
        encoding="utf-8",
    )
    (codex_dir / "verifier.toml").write_text(
        'name = "verifier"\n'
        'description = "Verify evidence independently."\n'
        'sandbox_mode = "read-only"\n'
        'developer_instructions = "Check acceptance criteria and report verdict, '
        'evidence, and unverified items."\n',
        encoding="utf-8",
    )
    mapping = tmp_path / "equivalence.yaml"
    _write_yaml(
        mapping,
        {
            "version": 1,
            "mappings": [
                {
                    "role": "verifier",
                    "claude": "verifier.md",
                    "codex": "verifier.toml",
                    "read_only": True,
                    "responsibilities": ["acceptance criteria"],
                    "required_output_fields": [
                        "verdict",
                        "evidence",
                        "unverified",
                    ],
                }
            ],
        },
    )

    assert compare_native_variants(claude_dir, codex_dir, mapping) == []


def test_native_equivalence_reports_missing_invariant_and_write_access(
    tmp_path: Path,
) -> None:
    claude_dir = tmp_path / "claude"
    codex_dir = tmp_path / "codex"
    claude_dir.mkdir()
    codex_dir.mkdir()
    (claude_dir / "critic.md").write_text(
        "---\nname: proposal-critic\ntools: [Read, Write]\n---\nNo contract.\n",
        encoding="utf-8",
    )
    (codex_dir / "critic.toml").write_text(
        'name = "proposal-critic"\ndeveloper_instructions = "No contract."\n',
        encoding="utf-8",
    )
    mapping = tmp_path / "equivalence.yaml"
    _write_yaml(
        mapping,
        {
            "version": 1,
            "mappings": [
                {
                    "role": "proposal-critic",
                    "claude": "critic.md",
                    "codex": "critic.toml",
                    "read_only": True,
                    "responsibilities": ["challenge assumptions"],
                    "required_output_fields": ["verdict"],
                }
            ],
        },
    )

    codes = {
        finding.code
        for finding in compare_native_variants(claude_dir, codex_dir, mapping)
    }
    assert codes == {
        "claude-agent-has-write-tool",
        "codex-agent-not-read-only",
        "missing-claude-output-field",
        "missing-claude-responsibility",
        "missing-codex-output-field",
        "missing-codex-responsibility",
    }
