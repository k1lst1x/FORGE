from fastapi.testclient import TestClient

from app.factory.scorecards import audit_grade, port_level, score_for_grade
from app.main import app


def test_audit_grade_rules() -> None:
    assert audit_grade(high=0, med=0) == "Gold"
    assert audit_grade(high=0, med=1) == "Silver"
    assert audit_grade(high=1, med=0) == "Bronze"
    assert score_for_grade("Gold") == 3
    assert score_for_grade("Bronze") == 1


def test_port_level_ladder() -> None:
    assert port_level(high=1, med=0) is None
    assert port_level(high=0, med=1) == "Bronze"
    assert port_level(high=0, med=0) == "Silver"
    assert port_level(high=0, med=0, tests_passing=True, verified_within_hour=True) == "Gold"


def test_observability_starts_without_alert() -> None:
    with TestClient(app) as client:
        response = client.get("/factory/observability")

    assert response.status_code == 200
    body = response.json()
    assert body["alert"]["would_fire"] is False
    assert body["scorecards"][0]["grade"] == "Gold"
    assert len(body["signoz_panels"]) == 5


def test_inject_mode_1_drops_to_bronze_and_would_fire() -> None:
    with TestClient(app) as client:
        response = client.post("/factory/inject", json={"mode": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["classification"] == "AUTOFIX_SAFE"
    snapshot = body["observability"]
    assert snapshot["alert"]["would_fire"] is True
    home = next(card for card in snapshot["scorecards"] if card["route"] == "/")
    assert home["grade"] == "Bronze"
    assert home["score"] == 1


def test_inject_mode_4_is_outage_and_does_not_fire() -> None:
    with TestClient(app) as client:
        response = client.post("/factory/inject", json={"mode": 4})

    assert response.status_code == 200
    body = response.json()
    assert body["classification"] == "UPSTREAM_OUTAGE"
    assert body["outage"] is True
    snapshot = body["observability"]
    assert snapshot["alert"]["would_fire"] is False
    assert snapshot["panels"]["triage"]["by_classification"]["UPSTREAM_OUTAGE"] >= 1


def test_restore_clears_injected_defects() -> None:
    with TestClient(app) as client:
        client.post("/factory/inject", json={"mode": 1})
        restored = client.post("/factory/restore")

    assert restored.status_code == 200
    snapshot = restored.json()["observability"]
    assert snapshot["alert"]["would_fire"] is False
    assert snapshot["outage"] in {False, 0}


def test_project_record_covers_named_criterion() -> None:
    with TestClient(app) as client:
        response = client.get("/factory/project")

    assert response.status_code == 200
    body = response.json()
    assert "goal" in body["properties"]
    assert "technical_choices" in body["properties"]
    assert "known_risks" in body["properties"]


def test_finding_intake_returns_immediately() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/factory/intake/finding",
            json={
                "alerts": [
                    {
                        "labels": {"route": "/products", "severity": "critical"},
                        "annotations": {"summary": "Security grade dropped below Silver"},
                    }
                ]
            },
        )

    assert response.status_code == 200
    assert response.json()["accepted"] is True


def test_inject_mode_2_does_not_fire_alert() -> None:
    with TestClient(app) as client:
        response = client.post("/factory/inject", json={"mode": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["classification"] == "FALSE_POSITIVE"
    assert body["observability"]["alert"]["would_fire"] is False


def test_port_bootstrap_skips_without_credentials() -> None:
    with TestClient(app) as client:
        response = client.post("/factory/port/bootstrap")

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
