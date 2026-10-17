from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TaskRequest(BaseModel):
    tenant: str = Field(min_length=1, max_length=100)
    skill: Literal["summarize", "classify", "extract"]
    payload: dict[str, Any]


class TaskResponse(BaseModel):
    id: str
    tenant: str
    skill: str
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None
    fallback: bool = False
    created_at: datetime
    completed_at: Optional[datetime] = None


class AuditEvent(BaseModel):
    id: int
    timestamp: datetime
    event_type: str
    task_id: Optional[str] = None
    tenant: Optional[str] = None
    skill: Optional[str] = None
    pool: Optional[str] = None
    reason: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
