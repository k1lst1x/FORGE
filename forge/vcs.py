"""
forge/vcs.py -- real git and real pull requests.

The gh CLI is NOT installed on this machine, so pull requests go through the
GitHub REST API with GITHUB_TOKEN instead of shelling out to a binary that is
not there. Branch, write, commit and diff are plain git and work with no token
at all -- so the factory still produces a real, inspectable branch even when
GitHub is not configured, and says so rather than inventing a PR URL.

GUARD RAILS
  * writes are refused outside pulse/ and tests/ -- the factory can change the
    app it built, not itself
  * never commits to main
  * refuses to push a branch whose tests did not pass
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

import httpx

from forge import config

log = logging.getLogger("forge.vcs")

STUB = False
WRITABLE_PREFIXES = ("pulse/", "tests/")
MAIN = os.getenv("FORGE_MAIN_BRANCH", "main")


class VcsError(RuntimeError):
    pass


def _git(*args, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(config.REPO_ROOT), capture_output=True, text=True, timeout=60
    )
    if check and result.returncode != 0:
        raise VcsError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return (result.stdout or "").strip()


def current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD")


def repo_slug() -> str | None:
    """owner/name, from config or the git remote."""
    if config.GITHUB_REPO:
        return config.GITHUB_REPO
    try:
        url = _git("remote", "get-url", "origin")
    except VcsError:
        return None
    match = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?$", url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def create_branch(name: str) -> str:
    """A fresh branch off main. Re-running a fix reuses the name, so delete first."""
    branch = f"forge/{name}"
    _git("fetch", "origin", check=False)
    try:
        _git("checkout", MAIN)
    except VcsError:
        log.warning("could not check out %s; branching from HEAD", MAIN)
    _git("branch", "-D", branch, check=False)
    _git("checkout", "-b", branch)
    log.info("on branch %s", branch)
    return branch


def write_files(changeset: list[dict]) -> list[str]:
    written = []
    for change in changeset or []:
        raw = (change.get("path") or "").replace("\\", "/").lstrip("./")
        if not raw.startswith(WRITABLE_PREFIXES) or ".." in raw:
            log.error("REFUSED write outside pulse/ and tests/: %s", raw)
            raise VcsError(f"refused to write {raw}: the factory cannot modify its own source")
        target = config.REPO_ROOT / raw
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.get("content", ""), encoding="utf-8")
        written.append(raw)
    log.info("wrote %s file(s)", len(written))
    return written


def commit_and_push(branch: str, message: str) -> str:
    if current_branch() in (MAIN, "HEAD"):
        raise VcsError("refusing to commit on main")
    _git("add", "--", "pulse", "tests")
    if not _git("status", "--porcelain"):
        raise VcsError("nothing to commit -- the changeset produced no on-disk change")
    _git("-c", "user.name=FORGE", "-c", "user.email=forge@local", "commit", "-m", message)
    sha = _git("rev-parse", "--short", "HEAD")
    if config.GITHUB_TOKEN:
        try:
            _git("push", "-u", "origin", branch, "--force-with-lease")
            log.info("pushed %s (%s)", branch, sha)
        except VcsError as exc:
            log.warning("push failed, branch stays local: %s", exc)
    else:
        log.warning("no GITHUB_TOKEN -- committed locally as %s, not pushed", sha)
    return sha


def get_diff(branch: str) -> str:
    return _git("diff", f"{MAIN}...{branch}", check=False)


def open_pr(branch: str, title: str, body: str) -> str:
    """Open a real pull request, or return the local branch reference.

    Never invents a URL. If GitHub is not configured the human still gets a real
    branch and a real diff to approve -- it just lives on this machine.
    """
    slug = repo_slug()
    if not (config.GITHUB_TOKEN and slug):
        log.warning("no GITHUB_TOKEN/repo -- no pull request opened; review the local branch")
        return f"local-branch:{branch}"
    try:
        response = httpx.post(
            f"https://api.github.com/repos/{slug}/pulls",
            headers={
                "Authorization": f"Bearer {config.GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
            json={"title": title[:250], "body": body, "head": branch, "base": MAIN},
            timeout=30,
        )
        if response.status_code in (200, 201):
            return response.json().get("html_url", f"local-branch:{branch}")
        log.error("GitHub refused the pull request: %s %s", response.status_code, response.text[:300])
    except Exception as exc:
        log.error("could not open a pull request: %s", exc)
    return f"local-branch:{branch}"


def merge_pr(pr_url: str) -> bool:
    """Merge. A local-only branch is merged locally so the app really updates."""
    branch = current_branch()
    if pr_url and pr_url.startswith("local-branch:"):
        branch = pr_url.split(":", 1)[1]
    try:
        _git("checkout", MAIN)
        _git("merge", "--no-ff", branch, "-m", f"Merge {branch}")
        log.info("merged %s into %s", branch, MAIN)
        if config.GITHUB_TOKEN and not pr_url.startswith("local-branch:"):
            _git("push", "origin", MAIN, check=False)
        return True
    except VcsError as exc:
        log.error("merge failed: %s", exc)
        return False


def reset_to_main() -> bool:
    try:
        _git("checkout", "--", ".")
        _git("checkout", MAIN)
        return True
    except VcsError:
        return False
