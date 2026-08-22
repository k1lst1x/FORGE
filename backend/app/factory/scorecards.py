"""Port Security Posture rules and the audit grade the SigNoz alert watches.

Audit metric (forge_security_grade): Gold=3, Silver=2, Bronze=1.
A drop below 2 (Bronze = one or more HIGH findings) is the Loop B trigger.

Port scorecard levels are the achievement ladder Damir's client grades against.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.factory.models import Finding, FindingSeverity

GRADE_SCORE = {"Gold": 3, "Silver": 2, "Bronze": 1}
ALERT_METRIC = "forge_security_grade"
ALERT_BELOW = 2

PORT_SCORECARD = {
    "identifier": "security_posture",
    "title": "Security Posture",
    "blueprint": "page",
    "levels": [
        {
            "title": "Failing",
            "color": "red",
            "rule": "default — one or more HIGH findings",
        },
        {
            "title": "Bronze",
            "color": "bronze",
            "rule": "open_findings_high == 0",
        },
        {
            "title": "Silver",
            "color": "silver",
            "rule": "Bronze passes AND open_findings_med == 0",
        },
        {
            "title": "Gold",
            "color": "gold",
            "rule": "Silver passes AND tests_passing",
        },
    ],
}


def audit_grade(*, high: int, med: int) -> str:
    if high > 0:
        return "Bronze"
    if med > 0:
        return "Silver"
    return "Gold"


def score_for_grade(grade: str) -> int:
    return GRADE_SCORE[grade]


def port_level(
    *,
    high: int,
    med: int,
    tests_passing: bool = False,
    verified_within_hour: bool = False,
) -> str | None:
    if high > 0:
        return None
    if med > 0:
        return "Bronze"
    if tests_passing and verified_within_hour:
        return "Gold"
    return "Silver"


def counts_by_route(findings: list[Finding]) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"HIGH": 0, "MED": 0, "LOW": 0})
    for finding in findings:
        grouped[finding.route][finding.severity.value] += finding.occurrences
    return dict(grouped)


def scorecards_for_findings(
    findings: list[Finding],
    *,
    routes: list[str] | None = None,
    tests_passing: bool = True,
    verified_within_hour: bool = False,
) -> list[dict[str, Any]]:
    grouped = counts_by_route(findings)
    route_names = list(dict.fromkeys([*(routes or []), *grouped.keys()]))
    if not route_names:
        route_names = ["/", "/products"]

    cards: list[dict[str, Any]] = []
    for route in route_names:
        counts = grouped.get(route, {"HIGH": 0, "MED": 0, "LOW": 0})
        grade = audit_grade(high=counts["HIGH"], med=counts["MED"])
        cards.append(
            {
                "route": route,
                "grade": grade,
                "score": score_for_grade(grade),
                "port_level": port_level(
                    high=counts["HIGH"],
                    med=counts["MED"],
                    tests_passing=tests_passing,
                    verified_within_hour=verified_within_hour,
                ),
                "open_findings_high": counts["HIGH"],
                "open_findings_med": counts["MED"],
                "open_findings_low": counts["LOW"],
                "alert_would_fire": score_for_grade(grade) < ALERT_BELOW,
            }
        )
    return cards


def worst_score(cards: list[dict[str, Any]]) -> int:
    if not cards:
        return GRADE_SCORE["Gold"]
    return min(int(card["score"]) for card in cards)


def severity_totals(findings: list[Finding]) -> dict[str, int]:
    totals = {severity.value: 0 for severity in FindingSeverity}
    for finding in findings:
        totals[finding.severity.value] += finding.occurrences
    return totals
