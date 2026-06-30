from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Mode = Literal["easy", "medium", "hard"]


@dataclass
class Alarm:
    id: str
    file: str
    rule: str
    line_start: int
    line_end: int
    message: str
    project: str = ""
    commit_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GWInput:
    build_semantic_graph: bool
    extract_constraints: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RepairContract:
    """Repair contract derived by GW from knowledge_pack + dependency graph."""
    must_fix: list[str] = field(default_factory=list)
    must_not_touch: list[str] = field(default_factory=list)
    allowed_transformations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def is_empty(self) -> bool:
        return not (self.must_fix or self.must_not_touch or self.allowed_transformations)


@dataclass
class GWProfileInput:
    """Projected input for GW Agent - only what GW needs from TaskProfiledEvent."""
    task_id: str
    mode: Mode
    alarm: Alarm
    gw_input: GWInput
    retry_index: int = 0
    introduced_rules: list[str] = field(default_factory=list)
    previous_audit_feedback: "PreviousAuditFeedback | None" = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KnowledgePack:
    """Knowledge pack for CP Agent containing full text content from OH-KB.

    Fields:
    - rule_templates: Full list of L2 rule template items from OH-KB
    - experiences: Full list of L3 repair experience items from OH-KB
    """
    rule_templates: list[dict] = field(default_factory=list)
    experiences: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PromptInjection:
    task_append: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CPInput:
    model_count: int
    models: list[str] = field(default_factory=list)
    prompt_injection: PromptInjection = field(default_factory=PromptInjection)
    knowledge_pack: KnowledgePack = field(default_factory=KnowledgePack)

    def __post_init__(self) -> None:
        if self.model_count != len(self.models):
            raise ValueError("cp_input.model_count must equal len(cp_input.models)")

    def to_dict(self) -> dict:
        return asdict(self)

    def to_worker_dict(self) -> dict:
        """
        Generate worker-visible version of CPInput.

        Excludes internal coordinator fields (models list) that workers don't need.
        knowledge_pack is intentionally excluded: GW synthesizes repair_contract
        (must_fix / must_not_touch / allowed_transformations) from knowledge_pack,
        so CP receives the unified contract from ContextReadyEvent instead.
        """
        return {
            "prompt_injection": asdict(self.prompt_injection),
            # "knowledge_pack": asdict(self.knowledge_pack),  # Moved to GW → repair_contract
        }


@dataclass
class IntroducedWarning:
    """Structured representation of a new warning introduced by a patch."""
    file: str
    line: int
    rule: str
    message: str
    repo_relative_file: str = ""
    code_snippet: str = ""  # Actual code at the problematic line (with context)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LinterViolation:
    """Structured representation of a linter violation detected during audit."""
    line: int
    column: int | None = None
    message: str = ""
    code_snippet: str = ""
    file: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PatchDiagnostic:
    patch_id: str
    failed_level: Literal["L1", "L2", "L3"]
    reason: str
    tool: str = ""
    details: str = ""
    # Enhanced structured fields for L3 failures
    introduced_warnings: list[IntroducedWarning] = field(default_factory=list)
    # Enhanced structured fields for L2 failures - specific violation locations
    linter_violations: list[LinterViolation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PreviousAuditFeedback:
    failed_level: Literal["L1", "L2", "L3"]
    reason: str
    failed_patches: list[str] = field(default_factory=list)
    patch_diagnostics: list[PatchDiagnostic] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TaskProfiledEvent:
    task_id: str
    retry_index: int
    mode: Mode
    alarm: Alarm
    gw_input: GWInput
    cp_input: CPInput
    previous_audit_feedback: PreviousAuditFeedback | None = None
    event: str = "task.profiled"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CPProfileInput:
    """Projected input for CP Agent - excludes GW-only control fields."""
    task_id: str
    retry_index: int
    mode: Mode
    alarm: Alarm
    cp_input: CPInput
    previous_audit_feedback: PreviousAuditFeedback | None = None
    event: str = "task.profiled"

    @classmethod
    def from_task_profiled(cls, profiled: TaskProfiledEvent) -> "CPProfileInput":
        return cls(
            task_id=profiled.task_id,
            retry_index=profiled.retry_index,
            mode=profiled.mode,
            alarm=profiled.alarm,
            cp_input=profiled.cp_input,
            previous_audit_feedback=profiled.previous_audit_feedback,
            event=profiled.event,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_worker_dict(self) -> dict:
        """
        Generate worker-visible version of CPProfileInput.

        Exposes only cp_input for workers.
        alarm is provided via show_alarm, and coordinator/internal fields are hidden.
        """
        return {
            "cp_input": self.cp_input.to_worker_dict(),
        }


@dataclass
class GraphSlice:
    graph_data: dict = field(default_factory=lambda: {
        "meta": {},
        "graph": {
            "files": [],
            "external_modules": [],
            "relations": {
                "file_to_file": [],
                "file_to_external": [],
                "module_to_module": [],
            },
            "anchors": {},
        },
    })


@dataclass
class CodeSnippet:
    """Code fragment selected by GW for precise repair context."""
    file_path: str
    snippet_type: str
    name: str
    start_line: int
    end_line: int
    content: str
    relevance: str
    relevance_reason: str
    confidence: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SemanticInfo:
    """Structured semantic hints carried from GW to CP."""
    type_dependencies: list[dict] = field(default_factory=list)
    inheritance_chain: list[dict] = field(default_factory=list)
    decorators: list[dict] = field(default_factory=list)
    api_signatures: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PreciseContextSlice:
    """GW precise context output. The raw BFS slice is retained for fallback/debugging."""
    task_id: str
    mode: Mode
    repair_contract: RepairContract = field(default_factory=RepairContract)
    raw_graph_slice: dict = field(default_factory=dict)
    rule_context_mode: str = ""
    slicing_strategy: str = "bfs_only"
    llm_reasoning: str = ""
    agent_confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ContextReadyEvent:
    """GW Agent output event containing context for CP.

    The primary content for CP is inside `precise_slice.repair_contract`.
    """
    task_id: str
    mode: Mode
    graph_slice: GraphSlice
    precise_slice: dict | None = None
    slicing_mode: str = "legacy"
    gw_trajectory_path: str | None = None
    event: str = "context.ready"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_worker_dict(self) -> dict:
        """Generate worker-visible version of ContextReadyEvent for CP Worker.

        Mode-aware projection:
        - easy:         graph_centric — alarm-centric BFS dependency graph (file list + edges)
        - medium/hard:  precise       — repair_contract (must_fix / must_not_touch /
                                        allowed_transformations) derived by GW from
                                        knowledge_pack + graph
        """
        if self.precise_slice:
            # medium / hard: GW-synthesized repair contract
            raw_contract = self.precise_slice.get("repair_contract", {})
            return {
                "context_mode": "precise",
                "repair_contract": {
                    "must_fix": raw_contract.get("must_fix", []),
                    "must_not_touch": raw_contract.get("must_not_touch", []),
                    "allowed_transformations": raw_contract.get("allowed_transformations", []),
                },
                "reasoning": self.precise_slice.get("llm_reasoning", ""),
            }

        # easy: alarm-centric dependency graph slice
        graph = self.graph_slice.graph_data.get("graph", {})
        files = [f.get("path", "") for f in graph.get("files", []) if f.get("path")]
        edges = [
            f"{e.get('source', '')} → {e.get('target', '')}"
            for e in graph.get("relations", {}).get("file_to_file", [])
            if e.get("source") and e.get("target")
        ]
        return {
            "context_mode": "graph_centric",
            "relevant_files": files,
            "dependency_edges": edges,
        }


@dataclass
class PatchItem:
    patch_id: str
    diff: str
    model_id: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PatchesReadyEvent:
    task_id: str
    mode: Mode
    patches: list[PatchItem] = field(default_factory=list)
    event: str = "patches.ready"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditDonePassedEvent:
    task_id: str
    patch_id: str
    event: str = "audit.done"
    result: str = "passed"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditDoneFailedEvent:
    task_id: str
    failed_level: Literal["L1", "L2", "L3"]
    reason: str
    failed_patches: list[str] = field(default_factory=list)
    patch_diagnostics: list[PatchDiagnostic] = field(default_factory=list)
    event: str = "audit.done"
    result: str = "failed"

    def to_dict(self) -> dict:
        return asdict(self)


AuditDoneEvent = AuditDonePassedEvent | AuditDoneFailedEvent


def parse_diff_by_file(diff_text: str) -> dict[str, str]:
    """Parse a unified diff into per-file diffs.

    Supports both formats:
    1) Multi-file git diff with `diff --git` headers.
    2) Unified diff blocks starting with `--- a/...` / `+++ b/...` only.
    """
    if not diff_text or not diff_text.strip():
        return {}

    def _normalize_path(raw_path: str) -> str:
        path = raw_path.split("\t", 1)[0].strip()
        if path.startswith("a/") or path.startswith("b/"):
            path = path[2:]
        return path

    result: dict[str, str] = {}
    lines = diff_text.splitlines()

    current_file = ""
    current_lines: list[str] = []
    current_has_diff_git = False

    def _flush_current() -> None:
        nonlocal current_file, current_lines, current_has_diff_git
        if current_file and current_lines:
            result[current_file] = "\n".join(current_lines) + "\n"
        current_file = ""
        current_lines = []
        current_has_diff_git = False

    for line in lines:
        if line.startswith("diff --git "):
            _flush_current()
            parts = line.split()
            if len(parts) >= 4:
                current_file = _normalize_path(parts[3])
            current_lines = [line]
            current_has_diff_git = True
            continue

        if line.startswith("--- "):
            # Header-only unified diff may use repeated `---` as file boundaries.
            if current_lines and not current_has_diff_git:
                _flush_current()
            if not current_lines:
                old_path = _normalize_path(line[4:])
                if old_path != "/dev/null":
                    current_file = old_path
                current_lines = [line]
                continue

        if line.startswith("+++ ") and current_lines and not current_has_diff_git and not current_file:
            # New file in header-only diff: old path is /dev/null, use +++ path.
            new_path = _normalize_path(line[4:])
            if new_path != "/dev/null":
                current_file = new_path

        if current_lines:
            current_lines.append(line)

    _flush_current()
    return result
