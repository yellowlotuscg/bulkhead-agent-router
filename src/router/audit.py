from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


class AuditStore:
    """Append-only SQLite audit log with database-enforced immutability."""

    def __init__(self, path: str = "data/audit.db") -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                task_id TEXT,
                tenant TEXT,
                skill TEXT,
                pool TEXT,
                reason TEXT,
                metadata TEXT NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TRIGGER IF NOT EXISTS audit_no_update
               BEFORE UPDATE ON audit_events BEGIN
                 SELECT RAISE(ABORT, 'audit log is append-only');
               END"""
        )
        self._conn.execute(
            """CREATE TRIGGER IF NOT EXISTS audit_no_delete
               BEFORE DELETE ON audit_events BEGIN
                 SELECT RAISE(ABORT, 'audit log is append-only');
               END"""
        )
        self._conn.commit()

    def append(
        self,
        event_type: str,
        *,
        task_id: Optional[str] = None,
        tenant: Optional[str] = None,
        skill: Optional[str] = None,
        pool: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> int:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO audit_events
                   (timestamp,event_type,task_id,tenant,skill,pool,reason,metadata)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    timestamp,
                    event_type,
                    task_id,
                    tenant,
                    skill,
                    pool,
                    reason,
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "task_id": row["task_id"],
                "tenant": row["tenant"],
                "skill": row["skill"],
                "pool": row["pool"],
                "reason": row["reason"],
                "metadata": json.loads(row["metadata"]),
            }
            for row in rows
        ]

    def close(self) -> None:
        self._conn.close()
