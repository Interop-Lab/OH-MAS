from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AlarmInstance:
    instance_id: str
    project: str
    rule_id: str
    target_file: str
    warning_message: str
    line_number: int
    start_line: int = 1
    end_line: int = 1
    commit_hash: str = ""


def load_instance(dataset_file: Path, instance_id: str) -> AlarmInstance:
    data = json.loads(dataset_file.read_text())
    if not isinstance(data, list):
        raise ValueError(f"Dataset must be a list: {dataset_file}")

    for row in data:
        if row.get("instance_id") == instance_id:
            return AlarmInstance(
                instance_id=row["instance_id"],
                project=row["project"],
                rule_id=row["rule_id"],
                target_file=row["target_file"],
                warning_message=row.get("warning_message", ""),
                line_number=int(row.get("line_number", 1)),
                start_line=int(row.get("start_line") or row.get("line_number") or 1),
                end_line=int(row.get("end_line") or row.get("line_number") or 1),
                commit_hash=str(row.get("commit_hash", "") or ""),
            )

    raise ValueError(f"Instance {instance_id} not found in {dataset_file}")
