from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


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
    _ = body
    slug = title.lower().replace(" ", "-")[:48]
    return f"https://github.com/k1lst1x/TheAgentHarnessHackathon2026/pull/stub-{branch}-{slug}"


def merge_pr(pr_url: str) -> bool:
    _ = pr_url
    return True
