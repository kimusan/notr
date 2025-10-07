from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional


@dataclass
class NotePayload:
    title: str
    body: str
    metadata: Optional[Dict[str, str]] = None


@dataclass
class Notebook:
    id: int
    uuid: str
    name: str
    created_at: datetime
    updated_at: datetime


@dataclass
class Note:
    id: int
    uuid: str
    notebook_id: int
    notebook_uuid: str
    notebook_name: str
    payload: NotePayload
    created_at: datetime
    updated_at: datetime
