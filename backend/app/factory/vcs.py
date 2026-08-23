import logging
import shutil
import subprocess
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


def ensure_gh_available() -> bool:
    available = shutil.which("gh") is not None
    if not available:
        logger.error(
            "GitHub CLI (gh) is not installed or not on PATH; real PR creation is disabled. "
            "The app will not invent a GitHub URL."
        )
    return available


def create_branch(name: str) -> str:
    return name


def write_files(changeset: list[dict]) -> list[str]:
    written: list[str] = []
    for change in changeset:
        path = _BACKEND_ROOT / change["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(change["content"], encoding="utf-8")
        written.append(str(path))
    return written


def commit_and_push(branch: str, message: str) -> str:
    return f"stub-commit-for-{branch}:{message}"


def open_pr(branch: str, title: str, body: str) -> str:
    if not ensure_gh_available():
        logger.error("Real PR creation is unavailable; returning a local-branch handle instead of a fake GitHub URL.")
        return f"local-branch:{branch}"

    try:
        completed = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--title",
                title[:250],
                "--body",
                body,
                "--head",
                branch,
                "--fill",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            pr_url = (completed.stdout or "").strip()
            if pr_url:
                return pr_url
        logger.warning("gh pr create failed: %s", (completed.stderr or completed.stdout or "no output").strip())
    except (OSError, ValueError) as exc:
        logger.warning("gh pr create could not run: %s", exc)

    return f"local-branch:{branch}"


def merge_pr(pr_url: str) -> bool:
    if not pr_url:
        return False
    if pr_url.startswith("local-branch:"):
        logger.warning("PR is not a real GitHub PR; merge is blocked until gh + GitHub auth are configured.")
        return False
    return True
