from __future__ import annotations

from pathlib import Path

import yaml
from pytest import MonkeyPatch

import agent_system.validation as validation
from agent_system.validation import (
    Finding,
    main,
    validate_claude_agent,
    validate_codex_agent,
    validate_skill,
)


def _valid_claude_agent(
    path: Path,
    *,
    name: str,
    tools: str,
    description: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    description = description or (
        "Use this agent when independent evidence must be assessed. "
        "Typical triggers include reviewing an authentication change; checking "
        'release evidence. See "When to invoke" in the agent body for worked scenarios.'
    )
    path.write_text(
        f"""---
name: {name}
description: {description}
model: inherit
color: red
tools: {tools}
---
You are an independent reviewer. You will remain inside the approved scope.

## When to invoke

- **Authentication review.** Review an authentication change independently.
- **Release evidence.** Check release evidence against acceptance criteria.

## Core responsibilities

1. You identify evidence-backed findings.
2. You avoid implementation work.

## Process

1. You read the acceptance criteria.
2. You inspect the evidence and report discrepancies.

## Quality standards

- You cite concrete evidence for every finding.
- You distinguish blockers from recommendations.

## Output format

Return a verdict, findings, evidence, and unresolved risks.

## Edge cases

- If evidence is absent, you report that gap without guessing.
""",
        encoding="utf-8",
    )


def test_read_only_claude_reviewer_rejects_write_tool(tmp_path: Path) -> None:
    path = tmp_path / "security-reviewer.md"
    _valid_claude_agent(
        path,
        name="security-reviewer",
        tools='["Read", "Write", "Grep"]',
    )

    assert [finding.code for finding in validate_claude_agent(path)] == [
        "read-only-agent-has-write-tool"
    ]


def test_valid_claude_reviewer_passes(tmp_path: Path) -> None:
    path = tmp_path / "security-reviewer.md"
    _valid_claude_agent(
        path,
        name="security-reviewer",
        tools='["Read", "Grep", "Glob"]',
    )

    assert validate_claude_agent(path) == []


def test_claude_description_rejects_empty_trigger_markers(tmp_path: Path) -> None:
    path = tmp_path / "security-reviewer.md"
    _valid_claude_agent(
        path,
        name="security-reviewer",
        tools='["Read", "Grep", "Glob"]',
        description=(
            'Use this agent when. Typical triggers include. See "When to invoke" '
            "in the agent body."
        ),
    )

    assert {
        "description-missing-trigger-conditions",
        "description-insufficient-typical-triggers",
    }.issubset({finding.code for finding in validate_claude_agent(path)})


def test_claude_description_requires_two_typical_trigger_scenarios(
    tmp_path: Path,
) -> None:
    path = tmp_path / "security-reviewer.md"
    _valid_claude_agent(
        path,
        name="security-reviewer",
        tools='["Read", "Grep", "Glob"]',
        description=(
            "Use this agent when independent evidence must be assessed. "
            "Typical triggers include reviewing authentication changes. "
            'See "When to invoke" in the agent body.'
        ),
    )

    assert "description-insufficient-typical-triggers" in {
        finding.code for finding in validate_claude_agent(path)
    }


def test_claude_description_accepts_semicolon_separated_typical_triggers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "security-reviewer.md"
    _valid_claude_agent(
        path,
        name="security-reviewer",
        tools='["Read", "Grep", "Glob"]',
        description=(
            "Use this agent when independent evidence must be assessed. "
            "Typical triggers include authentication reviews; release audits. "
            'See "When to invoke" in the agent body.'
        ),
    )

    assert validate_claude_agent(path) == []


def test_claude_description_accepts_unambiguous_official_list(tmp_path: Path) -> None:
    path = tmp_path / "security-reviewer.md"
    _valid_claude_agent(
        path,
        name="security-reviewer",
        tools='["Read", "Grep", "Glob"]',
        description=(
            "Use this agent when independent evidence must be assessed. "
            "Typical triggers include authentication reviews, release audits, and "
            'incident assessments. See "When to invoke" in the agent body.'
        ),
    )

    assert validate_claude_agent(path) == []


def test_claude_description_does_not_count_explanatory_commas_as_scenarios(
    tmp_path: Path,
) -> None:
    path = tmp_path / "security-reviewer.md"
    _valid_claude_agent(
        path,
        name="security-reviewer",
        tools='["Read", "Grep", "Glob"]',
        description=(
            "Use this agent when evidence must be assessed independently. "
            "Typical triggers include an authentication review, especially after a "
            'migration. See "When to invoke" in the agent body.'
        ),
    )

    assert "description-insufficient-typical-triggers" in {
        finding.code for finding in validate_claude_agent(path)
    }


def test_claude_description_requires_exact_prefix_and_marker_order(
    tmp_path: Path,
) -> None:
    descriptions = (
        "Do not use this agent when evidence is complete. Typical triggers include "
        'authentication reviews; release audits. See "When to invoke" in the body.',
        "First inspect the change. Use this agent when evidence must be assessed. "
        'Typical triggers include authentication reviews; release audits. See "When '
        'to invoke" in the body.',
        "Typical triggers include authentication reviews; release audits. Use this "
        'agent when evidence must be assessed. See "When to invoke" in the body.',
    )

    for index, description in enumerate(descriptions):
        path = tmp_path / f"security-reviewer-{index}.md"
        _valid_claude_agent(
            path,
            name=f"security-reviewer-{index}",
            tools='["Read", "Grep", "Glob"]',
            description=description,
        )
        assert "description-missing-trigger" in {
            finding.code for finding in validate_claude_agent(path)
        }


def test_claude_description_requires_when_pointer_at_end(tmp_path: Path) -> None:
    path = tmp_path / "security-reviewer.md"
    _valid_claude_agent(
        path,
        name="security-reviewer",
        tools='["Read", "Grep", "Glob"]',
        description=(
            "Use this agent when evidence must be assessed. "
            "Typical triggers include authentication reviews; release audits. "
            'See "When to invoke" in the body. Then implement the changes.'
        ),
    )

    assert "description-missing-when-pointer" in {
        finding.code for finding in validate_claude_agent(path)
    }


def test_claude_description_requires_exact_when_to_invoke_reference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "security-reviewer.md"
    _valid_claude_agent(
        path,
        name="security-reviewer",
        tools='["Read", "Grep", "Glob"]',
        description=(
            "Use this agent when independent evidence must be assessed. "
            "Typical triggers include authentication reviews and release audits. "
            'See "when to invoke" in the agent body.'
        ),
    )

    assert "description-missing-when-pointer" in {
        finding.code for finding in validate_claude_agent(path)
    }


def test_read_only_claude_reviewer_requires_explicit_tools(tmp_path: Path) -> None:
    path = tmp_path / "security-reviewer.md"
    _valid_claude_agent(
        path,
        name="security-reviewer",
        tools='["Read", "Grep", "Glob"]',
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'tools: ["Read", "Grep", "Glob"]\n', ""
        ),
        encoding="utf-8",
    )

    assert [finding.code for finding in validate_claude_agent(path)] == [
        "read-only-agent-missing-tool-restriction"
    ]


def test_claude_agent_reports_missing_trigger_and_prompt_contracts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "different.md"
    path.write_text(
        "---\nname: bad_name\ndescription: Generic helper\nmodel: unknown\n"
        "color: orange\n---\nI am helpful.\n",
        encoding="utf-8",
    )

    codes = {finding.code for finding in validate_claude_agent(path)}

    assert {
        "invalid-agent-name",
        "agent-name-path-mismatch",
        "description-missing-trigger",
        "description-missing-typical-triggers",
        "description-missing-when-pointer",
        "invalid-model",
        "invalid-color",
        "body-not-second-person",
        "missing-when-to-invoke",
        "missing-core-responsibilities",
        "missing-process",
        "missing-quality-standards",
        "missing-output-format",
        "missing-edge-cases",
        "agent-body-too-short",
    }.issubset(codes)


def test_codex_reviewer_requires_read_only_sandbox(tmp_path: Path) -> None:
    path = tmp_path / "verifier.toml"
    path.write_text(
        'name = "verifier"\ndescription = "Verify evidence"\n'
        'developer_instructions = "Remain read-only."\n',
        encoding="utf-8",
    )

    assert "missing-read-only-sandbox" in {
        finding.code for finding in validate_codex_agent(path)
    }


def test_valid_codex_reviewer_passes(tmp_path: Path) -> None:
    path = tmp_path / "proposal-critic.toml"
    path.write_text(
        'name = "proposal-critic"\n'
        'description = "Challenge an engineering proposal independently"\n'
        'developer_instructions = "You challenge assumptions and return evidence."\n'
        'sandbox_mode = "read-only"\n',
        encoding="utf-8",
    )

    assert validate_codex_agent(path) == []


def test_skill_requires_discovery_metadata_and_matching_folder(tmp_path: Path) -> None:
    skill = tmp_path / "right-name"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: wrong-name\ndescription: Does useful things\n---\n# Skill\n",
        encoding="utf-8",
    )

    codes = {finding.code for finding in validate_skill(skill)}

    assert "skill-name-folder-mismatch" in codes
    assert "skill-description-missing-discovery-trigger" in codes


def test_skill_requires_substantive_instructions(tmp_path: Path) -> None:
    skill = tmp_path / "reviewing-contracts"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: reviewing-contracts\n"
        "description: Use when API contracts change or compatibility is uncertain.\n"
        "---\n   \n",
        encoding="utf-8",
    )

    assert "skill-body-missing-instructions" in {
        finding.code
        for finding in validate_skill(
            skill, quick_validate_path=tmp_path / "missing-quick-validate.py"
        )
    }


def test_skill_rejects_atx_and_bold_only_headings(tmp_path: Path) -> None:
    validator = tmp_path / "quick_validate.py"
    validator.write_text("raise SystemExit(0)\n", encoding="utf-8")
    validator.chmod(0o644)
    for body in (
        "# Reviewing contracts\n## Process\n### Quality standards\n",
        "**Reviewing contracts**\n\n**Process:**\n\n**Quality standards**\n",
    ):
        skill = tmp_path / "reviewing-contracts"
        skill.mkdir(exist_ok=True)
        (skill / "SKILL.md").write_text(
            "---\nname: reviewing-contracts\n"
            "description: Use when API contracts change.\n---\n" + body,
            encoding="utf-8",
        )

        assert "skill-body-missing-instructions" in {
            finding.code
            for finding in validate_skill(skill, quick_validate_path=validator)
        }


def test_skill_accepts_instruction_lists_below_headings(tmp_path: Path) -> None:
    validator = tmp_path / "quick_validate.py"
    validator.write_text("raise SystemExit(0)\n", encoding="utf-8")
    validator.chmod(0o644)
    skill = tmp_path / "reviewing-contracts"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: reviewing-contracts\n"
        "description: Use when API contracts change.\n---\n"
        "# Reviewing contracts\n\n**Process:**\n\n"
        "- Inspect the public compatibility boundary.\n"
        "- Report every incompatible change.\n",
        encoding="utf-8",
    )

    assert validate_skill(skill, quick_validate_path=validator) == []


def test_skill_description_rejects_process_text_before_use_when(tmp_path: Path) -> None:
    skill = tmp_path / "reviewing-contracts"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: reviewing-contracts\n"
        "description: Inspect the inputs first. Use when API contracts change.\n"
        "---\n# Reviewing contracts\n\nInspect the public compatibility boundary.\n",
        encoding="utf-8",
    )

    assert "skill-description-missing-discovery-trigger" in {
        finding.code
        for finding in validate_skill(
            skill, quick_validate_path=tmp_path / "missing-quick-validate.py"
        )
    }


def test_skill_description_rejects_second_person_instead_of_conditions(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "reviewing-contracts"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: reviewing-contracts\n"
        "description: Use when You need to review an API contract.\n"
        "---\n# Reviewing contracts\n\nInspect the public compatibility boundary.\n",
        encoding="utf-8",
    )

    assert "skill-description-not-third-person" in {
        finding.code
        for finding in validate_skill(
            skill, quick_validate_path=tmp_path / "missing-quick-validate.py"
        )
    }


def test_skill_reports_missing_official_validator(tmp_path: Path) -> None:
    skill = tmp_path / "reviewing-contracts"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: reviewing-contracts\n"
        "description: Use when API contracts change or compatibility is uncertain.\n"
        "---\n# Reviewing contracts\n\nInspect the public compatibility boundary.\n",
        encoding="utf-8",
    )

    assert "official-skill-validator-unavailable" in {
        finding.code
        for finding in validate_skill(
            skill, quick_validate_path=tmp_path / "missing-quick-validate.py"
        )
    }


def test_skill_runs_readable_non_executable_official_validator(tmp_path: Path) -> None:
    validator = tmp_path / "quick_validate.py"
    validator.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "raise SystemExit(0 if Path(sys.argv[1]).name == 'reviewing-contracts' else 9)\n",
        encoding="utf-8",
    )
    validator.chmod(0o644)
    skill = tmp_path / "reviewing-contracts"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: reviewing-contracts\n"
        "description: Use when API contracts change or compatibility is uncertain.\n"
        "---\n# Reviewing contracts\n\nInspect the public compatibility boundary.\n",
        encoding="utf-8",
    )

    assert validate_skill(skill, quick_validate_path=validator) == []


def test_skill_reports_failure_from_readable_non_executable_validator(
    tmp_path: Path,
) -> None:
    validator = tmp_path / "quick_validate.py"
    validator.write_text(
        "import sys\n"
        "print(f'failure for {sys.argv[1]}', file=sys.stderr)\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    validator.chmod(0o644)
    skill = tmp_path / "reviewing-contracts"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: reviewing-contracts\n"
        "description: Use when API contracts change.\n---\n"
        "# Reviewing contracts\n\nInspect the public compatibility boundary.\n",
        encoding="utf-8",
    )

    findings = validate_skill(skill, quick_validate_path=validator)

    assert [finding.code for finding in findings] == [
        "official-skill-validation-failed"
    ]
    assert str(skill) in findings[0].message


def test_valid_skill_passes_with_injected_official_validator(tmp_path: Path) -> None:
    validator = tmp_path / "quick_validate.py"
    validator.write_text(
        "#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8"
    )
    validator.chmod(0o755)
    skill = tmp_path / "reviewing-contracts"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: reviewing-contracts\n"
        "description: Use when API contracts change or compatibility is uncertain.\n"
        "---\n# Reviewing contracts\n\nInspect the public compatibility boundary.\n",
        encoding="utf-8",
    )

    assert validate_skill(skill, quick_validate_path=validator) == []


def test_inventory_report_sanitizes_validator_paths(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    validator = tmp_path / "quick_validate.py"
    validator.write_text(
        "import sys\n"
        "print(f'failed validator {__file__} for {sys.argv[1]}', file=sys.stderr)\n"
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )
    validator.chmod(0o644)
    skill = tmp_path / "repo" / ".codex" / "skills" / "reviewing-contracts"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: reviewing-contracts\n"
        "description: Use when API contracts change.\n---\n"
        "# Reviewing contracts\n\nInspect the public compatibility boundary.\n",
        encoding="utf-8",
    )
    catalog = tmp_path / "catalog.toml"
    catalog.write_text(
        '[[roots]]\npath = "repo"\nowner = "repo"\nruntime = "codex"\n',
        encoding="utf-8",
    )
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "artifacts": [
                    {
                        "first_party": True,
                        "installations": [],
                        "kind": "skill",
                        "name": "reviewing-contracts",
                        "owner": "cuimo",
                        "source": "root-repo:.codex/skills/reviewing-contracts/SKILL.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = tmp_path / "report.yaml"
    monkeypatch.setattr(validation, "_find_quick_validate", lambda: validator)

    assert (
        main(
            [
                "--inventory",
                str(inventory),
                "--catalog",
                str(catalog),
                "--output",
                str(report),
            ]
        )
        == 1
    )

    rendered = report.read_text(encoding="utf-8")
    assert str(tmp_path) not in rendered
    assert "catalog:quick_validate.py" in rendered
    assert "root-repo:.codex/skills/reviewing-contracts" in rendered


def test_cli_resolves_portable_manifest_and_writes_deterministic_report(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    agent = repository / ".claude" / "agents" / "security-reviewer.md"
    _valid_claude_agent(
        agent,
        name="security-reviewer",
        tools='["Read", "Write", "Grep"]',
    )
    catalog = tmp_path / "catalog.toml"
    catalog.write_text(
        """[[roots]]
path = "repo"
owner = "repo"
runtime = "claude"
""",
        encoding="utf-8",
    )
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "artifacts": [
                    {
                        "first_party": True,
                        "installations": [
                            {
                                "owner": "repo",
                                "path": "root-repo:.claude/agents/security-reviewer.md",
                                "runtime": "claude",
                            }
                        ],
                        "kind": "agent",
                        "name": "security-reviewer",
                        "owner": "cuimo",
                        "source": "root-repo:.claude/agents/security-reviewer.md",
                    },
                    {
                        "first_party": False,
                        "installations": [],
                        "kind": "agent",
                        "name": "ignored-vendor-agent",
                        "owner": None,
                        "source": "root-repo:vendor/ignored.md",
                    },
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"

    assert (
        main(
            [
                "--inventory",
                str(inventory),
                "--catalog",
                str(catalog),
                "--output",
                str(first),
                "--first-party-only",
            ]
        )
        == 1
    )
    assert (
        main(
            [
                "--inventory",
                str(inventory),
                "--catalog",
                str(catalog),
                "--output",
                str(second),
                "--first-party-only",
            ]
        )
        == 1
    )

    assert first.read_bytes() == second.read_bytes()
    report = yaml.safe_load(first.read_text(encoding="utf-8"))
    assert report == {
        "findings": [
            {
                "code": "read-only-agent-has-write-tool",
                "message": "read-only agent grants write-capable tools: Write",
                "path": "root-repo:.claude/agents/security-reviewer.md",
            }
        ],
        "summary": {"artifacts": 1, "findings": 1},
    }


def test_cli_accepts_report_as_output_alias(tmp_path: Path) -> None:
    agent = tmp_path / "architecture-reviewer.md"
    _valid_claude_agent(
        agent,
        name="architecture-reviewer",
        tools='["Read", "Grep", "Glob"]',
    )
    report = tmp_path / "report.yaml"

    assert main(["--paths", str(agent), "--report", str(report)]) == 0

    assert yaml.safe_load(report.read_text(encoding="utf-8"))["summary"] == {
        "artifacts": 1,
        "findings": 0,
    }


def test_cli_rejects_markdown_agent_installed_as_codex(tmp_path: Path) -> None:
    agent = tmp_path / "repo" / ".codex" / "agents" / "reviewer.md"
    _valid_claude_agent(agent, name="reviewer", tools='["Read", "Grep", "Glob"]')
    catalog = tmp_path / "catalog.toml"
    catalog.write_text(
        '[[roots]]\npath = "repo"\nowner = "repo"\nruntime = "codex"\n',
        encoding="utf-8",
    )
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "artifacts": [
                    {
                        "first_party": True,
                        "installations": [
                            {
                                "owner": "repo",
                                "path": "root-repo:.codex/agents/reviewer.md",
                                "runtime": "codex",
                            }
                        ],
                        "kind": "agent",
                        "name": "reviewer",
                        "owner": "cuimo",
                        "source": "root-repo:.codex/agents/reviewer.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = tmp_path / "report.yaml"

    assert (
        main(
            [
                "--inventory",
                str(inventory),
                "--catalog",
                str(catalog),
                "--output",
                str(report),
                "--first-party-only",
            ]
        )
        == 1
    )
    assert yaml.safe_load(report.read_text(encoding="utf-8"))["findings"] == [
        {
            "code": "invalid-codex-agent-extension",
            "message": "Codex agents must use native TOML definitions",
            "path": "root-repo:.codex/agents/reviewer.md",
        }
    ]


def test_finding_is_immutable_and_orderable() -> None:
    assert Finding("b", Path("z"), "m") > Finding("a", Path("z"), "m")
