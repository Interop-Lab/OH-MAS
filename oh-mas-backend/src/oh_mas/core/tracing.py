from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "to_dict"):
        return to_jsonable(value.to_dict())
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump())
    if hasattr(value, "dict"):
        return to_jsonable(value.dict())
    if hasattr(value, "__dataclass_fields__"):
        return to_jsonable(asdict(value))
    if hasattr(value, "json"):
        try:
            return json.loads(value.json())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return to_jsonable(vars(value))
    return repr(value)


@dataclass
class TraceEntry:
    seq: int
    ts: str
    kind: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "kind": self.kind,
            "payload": to_jsonable(self.payload),
        }


class TraceRecorder:
    def __init__(self, *, task_id: str, trace_root: Path | None = None):
        self.task_id = task_id
        self.trace_root = trace_root
        self.entries: list[dict[str, Any]] = []
        self._seq = 0
        self._trace_file: Path | None = None
        if trace_root is not None:
            if trace_root.exists():
                shutil.rmtree(trace_root, ignore_errors=True)
            trace_root.mkdir(parents=True, exist_ok=True)
            self._trace_file = trace_root / "trace.jsonl"

    def record(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        entry = TraceEntry(seq=self._seq, ts=utc_now_iso(), kind=kind, payload=payload).to_dict()
        self.entries.append(entry)
        if self._trace_file is not None:
            with self._trace_file.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
