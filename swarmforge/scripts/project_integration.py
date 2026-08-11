#!/usr/bin/env python3
"""Install ignored project integration and user-local SwarmForge commands."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import shutil


FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
CODEGRAPH_TOOLS = (
    "mcp__codegraph__codegraph_search",
    "mcp__codegraph__codegraph_context",
    "mcp__codegraph__codegraph_callers",
    "mcp__codegraph__codegraph_callees",
    "mcp__codegraph__codegraph_impact",
    "mcp__codegraph__codegraph_node",
    "mcp__codegraph__codegraph_explore",
    "mcp__codegraph__codegraph_files",
    "mcp__codegraph__codegraph_status",
)
LOCAL_EXCLUDES = (
    ".codex/",
    ".claude/",
    ".swarmforge/",
    ".codegraph/",
    ".worktrees/",
    ".paseo/",
)


def _managed_hook(command: str, *, matcher: str | None, timeout: int) -> dict:
    hook = {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": timeout,
                "statusMessage": "Running deterministic Python gate",
            }
        ]
    }
    if matcher is not None:
        hook["matcher"] = matcher
    return hook


def _is_managed_hook(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks", [])
    return isinstance(hooks, list) and any(
        isinstance(hook, dict)
        and isinstance(command := hook.get("command"), str)
        and (" gate fast" in command or " gate stop" in command or "gate.py" in command)
        for hook in hooks
    )


def _merge_hooks(
    document: dict[str, object], *, cli_command: str, codex: bool
) -> dict[str, object]:
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
    managed = {
        "PostToolUse": _managed_hook(
            f"{cli_command} gate fast",
            matcher="apply_patch|Edit|Write" if codex else "Edit|Write",
            timeout=30,
        ),
        "Stop": _managed_hook(f"{cli_command} gate stop", matcher=None, timeout=300),
    }
    for event, entry in managed.items():
        existing = hooks.get(event, [])
        retained = (
            [item for item in existing if not _is_managed_hook(item)]
            if isinstance(existing, list)
            else []
        )
        hooks[event] = [*retained, entry]
    document["hooks"] = hooks
    return document


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    document = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return document


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def integrate_project(root: Path, *, cli_path: Path) -> tuple[str, ...]:
    """Install idempotent, ignored Codex/Claude integration for one project."""
    root = root.resolve()
    cli_command = shlex.quote(str(cli_path.resolve()))
    codex_hooks = root / ".codex/hooks.json"
    claude_settings = root / ".claude/settings.json"
    codex_config = root / ".codex/config.toml"
    gate_config = root / ".swarmforge/python-gates.toml"
    runtime = root / ".swarmforge/runtime"

    codex = _read_json(codex_hooks)
    codex.setdefault("description", "SwarmForge deterministic project hooks.")
    _write_json(codex_hooks, _merge_hooks(codex, cli_command=cli_command, codex=True))

    claude = _read_json(claude_settings)
    permissions = claude.get("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {}
    allowed = permissions.get("allow", [])
    if not isinstance(allowed, list):
        allowed = []
    permissions["allow"] = list(
        dict.fromkeys(
            [*(item for item in allowed if isinstance(item, str)), *CODEGRAPH_TOOLS]
        )
    )
    claude["permissions"] = permissions
    _write_json(
        claude_settings,
        _merge_hooks(claude, cli_command=cli_command, codex=False),
    )

    codegraph_config = (
        '[mcp_servers.codegraph]\ncommand = "codegraph"\nargs = ["serve", "--mcp"]\n'
    )
    codex_config.parent.mkdir(parents=True, exist_ok=True)
    if not codex_config.is_file():
        codex_config.write_text(codegraph_config)
    elif "[mcp_servers.codegraph]" not in codex_config.read_text():
        with codex_config.open("a") as stream:
            stream.write("\n" + codegraph_config)

    gate_config.parent.mkdir(parents=True, exist_ok=True)
    if not gate_config.is_file():
        gate_config.write_text(
            """[gate]
max_output_bytes = 8192
timeout_seconds = 1800

[affected_tests]
enabled = true
timeout_seconds = 30
depth = 2

[commands]
hardening = [
  ["uv", "run", "pytest", "-q", "--cov=src", "--cov-branch", "--crap", "--crap-threshold=30", "--crap-top-n=20"],
  ["uv", "run", "xenon", "--max-absolute", "C", "--max-modules", "B", "--max-average", "A", "src"],
  ["uv", "run", "pylint", "--disable=all", "--enable=duplicate-code", "src"],
  ["uv", "run", "mutmut", "run"],
]
"""
        )

    source = FRAMEWORK_ROOT / "swarmforge"
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("swarmforge.conf", "backends.toml", "constitution.prompt"):
        shutil.copy2(source / name, runtime / name)
    for name in ("constitution", "roles"):
        shutil.copytree(source / name, runtime / name, dirs_exist_ok=True)
    registry_text = (
        (source / "project-agents.toml")
        .read_text()
        .replace(
            'prompt = "swarmforge/roles/',
            'prompt = ".swarmforge/runtime/roles/',
        )
    )
    config_text = (source / "swarmforge.conf").read_text()
    for agent_file in sorted((root / ".claude/agents").glob("*.md")):
        name = agent_file.stem
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name) is None:
            continue
        shutil.copy2(agent_file, runtime / "roles" / f"{name}.prompt")
        if f"[agents.{name}]" not in registry_text:
            registry_text += (
                f"\n[agents.{name}]\n"
                f'role = "{name}"\n'
                'backend_instance = "claude-trabajo"\n'
                'mode = "lazy"\n'
                f'prompt = ".swarmforge/runtime/roles/{name}.prompt"\n'
                'tags = ["project-local"]\n'
            )
        if not re.search(
            rf"(?:^|\n)(?:lazy-)?window\s+{re.escape(name)}\s", config_text
        ):
            config_text += f"lazy-window {name} claude-trabajo {name} batch\n"
    (runtime / "project-agents.toml").write_text(registry_text)
    (runtime / "swarmforge.conf").write_text(config_text)

    exclude = root / ".git/info/exclude"
    if exclude.parent.is_dir():
        marker = "# SwarmForge local integration"
        current = exclude.read_text() if exclude.is_file() else ""
        if marker not in current:
            separator = "" if not current or current.endswith("\n") else "\n"
            exclude.write_text(
                current
                + separator
                + marker
                + "\n"
                + "".join(f"/{item}\n" for item in LOCAL_EXCLUDES)
            )
    return tuple(
        str(path.relative_to(root))
        for path in (codex_hooks, codex_config, claude_settings, gate_config, runtime)
    )


def install_cli(*, bin_dir: Path, framework_root: Path = FRAMEWORK_ROOT) -> None:
    """Install stable user-local symlinks without replacing unrelated commands."""
    bin_dir = bin_dir.expanduser().resolve()
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("swarm", "close-swarm"):
        source = (framework_root / name).resolve()
        destination = bin_dir / name
        if destination.is_symlink() and destination.resolve() == source:
            continue
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"refusing to replace {destination}")
        destination.symlink_to(source)
