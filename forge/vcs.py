"""forge/vcs.py — git branch / commit / PR.  OWNER: DAMIR.

STUB from the §08 stub session. Signatures FROZEN — Damir fills in the bodies
(Block 2). The engine only ever calls these names, so his real implementation
drops in with no change on my side.
"""
from __future__ import annotations

from forge import telemetry

STUB = True


def create_branch(name: str) -> str:
    telemetry.counter("forge_vcs_calls_total", 1, fn="create_branch")
    return name


def write_files(changeset: list[dict]) -> list[str]:
    # Damir's real one refuses any path outside pulse/ and tests/ and logs the attempt.
    return [c["path"] for c in changeset]


def commit_and_push(branch: str, message: str) -> str:
    return "abc1234"


def open_pr(branch: str, title: str, body: str) -> str:
    return "https://github.com/x/y/pull/1"


def merge_pr(pr_url: str) -> bool:
    return True


def get_diff(branch: str) -> str:
    return "diff --git a/pulse/routes/example.py b/pulse/routes/example.py\n+ stub diff\n"


def reset_to_main() -> bool:
    return True
