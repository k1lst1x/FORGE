import logging
import shutil
import subprocess
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


def ensure_gh_available() -> bool:
    available = shutil.which("gh") is not None
    if not available:
        logger.warning(
            "GitHub CLI (gh) is not installed or not on PATH; PR creation will fall back to a local branch URL."
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
        slug = title.lower().replace(" ", "-")[:48]
        return f"https://github.com/k1lst1x/TheAgentHarnessHackathon2026/pull/stub-{branch}-{slug}"

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

    slug = title.lower().replace(" ", "-")[:48]
    return f"https://github.com/k1lst1x/TheAgentHarnessHackathon2026/pull/stub-{branch}-{slug}"


def merge_pr(pr_url: str) -> bool:
    if not pr_url:
        return False
    return True
