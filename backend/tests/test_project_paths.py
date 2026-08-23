"""The factory must be able to FIND its own configuration.

watchers/, policy/ and contracts/ live at the repo root; the code lives under
backend/. When REPO_ROOT was used to resolve them, every lookup landed on a
path that does not exist and the loaders did the polite thing: logged one line
and returned {}. The scraper had no target and no contract, the auditor had no
checks, and nothing raised -- so it read as "working, just quiet".

These tests fail loudly instead.
"""

from pathlib import Path

from app import audit, brightdata, config


def test_project_root_is_where_the_config_actually_lives() -> None:
    assert (config.PROJECT_ROOT / "watchers").is_dir()
    assert (config.PROJECT_ROOT / "policy").is_dir()
    assert (config.PROJECT_ROOT / "contracts").is_dir()


def test_watcher_resolves_and_is_not_silently_empty() -> None:
    assert brightdata.WATCHER_PATH.exists(), brightdata.WATCHER_PATH

    watcher = brightdata.watcher()
    assert watcher, "watcher() returned {} -- the scraper is configured out of existence"
    assert watcher["target_url"]
    assert watcher["collector_id"]


def test_pinned_collector_is_the_one_claude_md_pins() -> None:
    """Never create a new collector when a pinned one exists -- generation takes
    5-10 minutes, so a stray `create` on the demo path is a demo that hangs."""
    assert brightdata.collector_id() == "c_mt4y9wy817vo82ojy8"


def test_contract_resolves_and_carries_its_thresholds() -> None:
    contract = brightdata.contract()
    assert contract, "contract() returned {} -- nothing would be validated before reaching data/"
    assert contract["min_rows"] > 0
    assert "title" in contract["items"]["properties"]


def test_audit_policy_resolves_with_every_check() -> None:
    checks = audit.load_policy().get("checks") or []
    ids = [c["id"] for c in checks]

    assert len(ids) == 19, f"expected 19 checks, loaded {len(ids)}"
    # D1/D2 are the data-freshness and contract checks the scrape pipeline
    # depends on; their absence is what made a stale feed look healthy.
    assert {"S1", "Q1", "P1", "D1", "D2"} <= set(ids)


def test_policy_path_is_absolute_so_cwd_cannot_change_the_answer() -> None:
    assert Path(config.POLICY_PATH).is_absolute()
    assert Path(config.POLICY_PATH).exists()


def test_scrape_output_lands_at_the_project_root_not_under_backend() -> None:
    from app import store

    path = store._scrape_path(brightdata.watcher())
    assert path.is_relative_to(config.PROJECT_ROOT)
    assert not path.is_relative_to(config.PROJECT_ROOT / "backend")
