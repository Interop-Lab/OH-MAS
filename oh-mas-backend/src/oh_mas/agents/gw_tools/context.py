from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oh_mas.core.schemas import Alarm, KnowledgePack, Mode, PreviousAuditFeedback, RepairContract


@dataclass
class GWToolContext:
    task_id: str
    mode: Mode
    alarm: Alarm
    graph_data: dict
    rule_mode: dict
    knowledge_pack: KnowledgePack | None = None
    repo_root: str = ""
    phase: str = "main"  # "llm" | "fallback" | "main" - 标记当前执行阶段
    retry_index: int = 0
    introduced_rules: list[str] = field(default_factory=list)
    previous_audit_feedback: PreviousAuditFeedback | None = None
    trace_steps: list[dict[str, Any]] = field(default_factory=list)
    repair_contract: RepairContract = field(default_factory=RepairContract)

    @property
    def sliced_graph(self) -> dict:
        return self.graph_data.get("graph", {})

    @property
    def repo_path(self) -> Path | None:
        return Path(self.repo_root).resolve() if self.repo_root else None

    def record_step(self, tool: str, output: Any, **input_payload: Any) -> Any:
        self.trace_steps.append(
            {
                "step": len(self.trace_steps) + 1,
                "phase": self.phase,
                "tool": tool,
                "input": input_payload,
                "output": output,
            }
        )
        return output

    def reset_collected_state(self) -> None:
        """Reset collected state for fallback scenario. Keeps trace_steps for debugging."""
        self.repair_contract = RepairContract()

    def read_file_lines(self, file_path: str, start_line: int, end_line: int) -> str | None:
        """Read specific lines from a file in the repo. Returns None if unavailable."""
        root = self.repo_path
        if root is None:
            return None
        full_path = (root / file_path).resolve()
        try:
            if not full_path.is_relative_to(root) or not full_path.is_file():
                return None
        except ValueError:
            return None
        try:
            lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
            actual_start = max(1, start_line)
            actual_end = min(len(lines), end_line)
            if actual_start > actual_end:
                return ""
            return "\n".join(lines[actual_start - 1:actual_end])
        except Exception:
            return None
