import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.factory.models import (
    FactoryRun,
    FactoryRunDetail,
    FactoryRunStatus,
    FactoryStep,
    FactoryStepStatus,
    Finding,
    FindingSeverity,
    IntakeType,
)


def _database_path() -> Path:
    return Path(settings.database_path)


def _connect() -> sqlite3.Connection:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    db = _connect()
    try:
        yield db
        db.commit()
    finally:
        db.close()


def init_db() -> None:
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS factory_runs (
                id TEXT PRIMARY KEY,
                intake TEXT NOT NULL,
                title TEXT NOT NULL,
                brief TEXT,
                trigger TEXT NOT NULL,
                status TEXT NOT NULL,
                next_gate TEXT,
                branch TEXT,
                pr_url TEXT,
                trace_id TEXT,
                outcome TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS factory_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY(run_id) REFERENCES factory_runs(id)
            );

            CREATE TABLE IF NOT EXISTS findings (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                check_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                route TEXT NOT NULL,
                title TEXT NOT NULL,
                evidence TEXT NOT NULL,
                suggested_fix_hint TEXT,
                occurrences INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES factory_runs(id)
            );
            """
        )


def create_run(
    *,
    run_id: str,
    title: str,
    brief: str | None,
    trigger: str,
    intake: IntakeType = IntakeType.brief,
) -> FactoryRun:
    with connection() as db:
        db.execute(
            """
            INSERT INTO factory_runs (id, intake, title, brief, trigger, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, intake.value, title, brief, trigger, FactoryRunStatus.planned.value),
        )
    return get_run(run_id)


def list_runs() -> list[FactoryRun]:
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM factory_runs ORDER BY datetime(created_at) DESC, id DESC"
        ).fetchall()
    return [_run_from_row(row) for row in rows]


def get_run(run_id: str) -> FactoryRun:
    with connection() as db:
        row = db.execute("SELECT * FROM factory_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(run_id)
    return _run_from_row(row)


def get_run_detail(run_id: str) -> FactoryRunDetail:
    run = get_run(run_id)
    return FactoryRunDetail(
        **run.model_dump(),
        steps=list_steps(run_id),
        findings=list_findings(run_id),
    )


def update_run(run_id: str, **fields: Any) -> FactoryRun:
    allowed = {"status", "next_gate", "branch", "pr_url", "trace_id", "outcome"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return get_run(run_id)

    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = [
        value.value if hasattr(value, "value") else value
        for value in updates.values()
    ]

    with connection() as db:
        db.execute(
            f"""
            UPDATE factory_runs
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*values, run_id),
        )
    return get_run(run_id)


def start_step(run_id: str, name: str) -> FactoryStep:
    with connection() as db:
        cursor = db.execute(
            """
            INSERT INTO factory_steps (run_id, name, status, started_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (run_id, name, FactoryStepStatus.running.value),
        )
        step_id = cursor.lastrowid
    return get_step(step_id)


def complete_step(
    step_id: int,
    *,
    status: FactoryStepStatus = FactoryStepStatus.completed,
    summary: str | None = None,
) -> FactoryStep:
    with connection() as db:
        db.execute(
            """
            UPDATE factory_steps
            SET status = ?, summary = ?, completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status.value, summary, step_id),
        )
    return get_step(step_id)


def get_step(step_id: int) -> FactoryStep:
    with connection() as db:
        row = db.execute("SELECT * FROM factory_steps WHERE id = ?", (step_id,)).fetchone()
    if row is None:
        raise KeyError(step_id)
    return _step_from_row(row)


def list_steps(run_id: str) -> list[FactoryStep]:
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM factory_steps WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    return [_step_from_row(row) for row in rows]


def save_finding(
    *,
    finding_id: str,
    run_id: str,
    check_id: str,
    severity: FindingSeverity,
    route: str,
    title: str,
    evidence: str,
    suggested_fix_hint: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Finding:
    with connection() as db:
        db.execute(
            """
            INSERT INTO findings (
                id, run_id, check_id, severity, route, title, evidence,
                suggested_fix_hint, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding_id,
                run_id,
                check_id,
                severity.value,
                route,
                title,
                evidence,
                suggested_fix_hint,
                json.dumps(metadata or {}),
            ),
        )
    return get_finding(finding_id)


def get_finding(finding_id: str) -> Finding:
    with connection() as db:
        row = db.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if row is None:
        raise KeyError(finding_id)
    return _finding_from_row(row)


def list_findings(run_id: str | None = None, route: str | None = None) -> list[Finding]:
    query = "SELECT * FROM findings"
    clauses: list[str] = []
    values: list[str] = []
    if run_id is not None:
        clauses.append("run_id = ?")
        values.append(run_id)
    if route is not None:
        clauses.append("route = ?")
        values.append(route)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY datetime(created_at) DESC, id DESC"

    with connection() as db:
        rows = db.execute(query, values).fetchall()
    return [_finding_from_row(row) for row in rows]


def _run_from_row(row: sqlite3.Row) -> FactoryRun:
    return FactoryRun(
        id=row["id"],
        intake=IntakeType(row["intake"]),
        title=row["title"],
        brief=row["brief"],
        trigger=row["trigger"],
        status=FactoryRunStatus(row["status"]),
        next_gate=row["next_gate"],
        branch=row["branch"],
        pr_url=row["pr_url"],
        trace_id=row["trace_id"],
        outcome=row["outcome"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _step_from_row(row: sqlite3.Row) -> FactoryStep:
    return FactoryStep(
        id=row["id"],
        run_id=row["run_id"],
        name=row["name"],
        status=FactoryStepStatus(row["status"]),
        summary=row["summary"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _finding_from_row(row: sqlite3.Row) -> Finding:
    return Finding(
        id=row["id"],
        run_id=row["run_id"],
        check_id=row["check_id"],
        severity=FindingSeverity(row["severity"]),
        route=row["route"],
        title=row["title"],
        evidence=row["evidence"],
        suggested_fix_hint=row["suggested_fix_hint"],
        occurrences=row["occurrences"],
        created_at=row["created_at"],
    )
