from app.factory.models import ChangeRequest


def upsert_run(cr: ChangeRequest) -> str:
    return f"port-run-{cr.run_id}"


def update_scorecard(route: str, grade: str, findings: list[dict]) -> None:
    _ = (route, grade, findings)


def request_approval(cr: ChangeRequest) -> str:
    return f"approval-{cr.run_id}"


def wait_for_approval(approval_id: str) -> bool:
    _ = approval_id
    return False


def escalate(cr: ChangeRequest, reason: str) -> str:
    _ = cr
    return f"escalation-{reason.lower().replace(' ', '-')[:48]}"
