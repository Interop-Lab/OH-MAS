"""Append-only ledger of typed audit failures (the paper's H_t).

Constraints are derived deterministically from DA diagnostics, never from LLM text, so the set can only grow:
H_{t+1} = H_t ∪ {e_{t+1}}. Entries are keyed so repeats update bookkeeping instead of adding duplicates.
A ledger entry is a forbidden *outcome* (a residual alarm that must go, a regression that must not appear),
not a region that may not be edited.
"""
from __future__ import annotations

import re
from dataclasses import replace

from oh_mas.core.schemas import Alarm, AuditDoneFailedEvent, RepairContract, TypedConstraint

_EVIDENCE_CAP = 400
_PROMPT_EVIDENCE_CAP = 300
_TAG_RE = re.compile(r"^\[H:t(\d+):(L[123])\]")


def constraint_tag(c: TypedConstraint) -> str:
    """`t` is the attempt whose failure produced the entry (F_t -> e_{t+1})."""
    return f"[H:t{c.round_added}:{c.level}]"


def is_ledger_item(text: str) -> bool:
    return bool(_TAG_RE.match(text or ""))


def _clip(text: str, cap: int = _EVIDENCE_CAP) -> str:
    text = (text or "").strip()
    return text if len(text) <= cap else text[:cap] + "…"


def _first_line(text: str, cap: int = 160) -> str:
    lines = (text or "").strip().splitlines()
    return lines[0][:cap] if lines else ""


def constraints_from_audit(audit: AuditDoneFailedEvent, *, attempt: int, alarm: Alarm) -> list[TypedConstraint]:
    out: list[TypedConstraint] = []
    for diag in audit.patch_diagnostics:
        if diag.failed_level == "L1":
            reason = diag.reason or audit.reason
            out.append(
                TypedConstraint(
                    key=f"L1|{_first_line(reason)}",
                    level="L1",
                    round_added=attempt,
                    rule=alarm.rule,
                    statement=(
                        "The patch must apply cleanly: copy every old_str verbatim from the CURRENT file "
                        "content (an earlier patch failed to apply)."
                    ),
                    evidence=_clip(reason + (f"\n{diag.details}" if diag.details else "")),
                    patch_id=diag.patch_id,
                    last_seen_round=attempt,
                )
            )
        elif diag.failed_level == "L2":
            if diag.linter_violations:
                for v in diag.linter_violations:
                    file = v.file or alarm.file
                    out.append(
                        TypedConstraint(
                            key=f"L2|{alarm.rule}|{file}|{v.line}",
                            level="L2",
                            round_added=attempt,
                            rule=alarm.rule,
                            file=file,
                            line=v.line,
                            statement=(
                                f"Target alarm `{alarm.rule}` must be eliminated at {file}:{v.line} "
                                f"(still reported after attempt {attempt})."
                            ),
                            evidence=_clip("\n".join(x for x in (v.message, v.code_snippet) if x)),
                            patch_id=diag.patch_id,
                            last_seen_round=attempt,
                        )
                    )
            else:
                out.append(
                    TypedConstraint(
                        key=f"L2|{alarm.rule}|{alarm.file}|-",
                        level="L2",
                        round_added=attempt,
                        rule=alarm.rule,
                        file=alarm.file,
                        statement=(
                            f"Target alarm `{alarm.rule}` must be eliminated in {alarm.file} "
                            f"(still reported after attempt {attempt})."
                        ),
                        evidence=_clip(diag.reason),
                        patch_id=diag.patch_id,
                        last_seen_round=attempt,
                    )
                )
        elif diag.failed_level == "L3":
            if diag.introduced_warnings:
                for w in diag.introduced_warnings:
                    file = w.repo_relative_file or w.file
                    out.append(
                        TypedConstraint(
                            key=f"L3|{w.rule}|{file}",
                            level="L3",
                            round_added=attempt,
                            rule=w.rule,
                            file=file,
                            line=w.line,
                            statement=f"Do not introduce `{w.rule}` in {file} (a previous patch caused it at line {w.line}).",
                            evidence=_clip("\n".join(x for x in (w.message, w.code_snippet) if x)),
                            patch_id=diag.patch_id,
                            last_seen_round=attempt,
                        )
                    )
            else:
                out.append(
                    TypedConstraint(
                        key=f"L3|?|{_first_line(diag.reason)}",
                        level="L3",
                        round_added=attempt,
                        statement="Do not introduce new linter warnings (a previous patch caused a regression).",
                        evidence=_clip(diag.reason),
                        patch_id=diag.patch_id,
                        last_seen_round=attempt,
                    )
                )
    return out


class ConstraintLedger:
    """Insertion-ordered, append-only set of TypedConstraint."""

    def __init__(self) -> None:
        self._entries: dict[str, TypedConstraint] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def update(self, audit: AuditDoneFailedEvent, *, attempt: int, alarm: Alarm) -> list[TypedConstraint]:
        """Fold one failed audit into the ledger; returns only the entries that are new."""
        added: list[TypedConstraint] = []
        for c in constraints_from_audit(audit, attempt=attempt, alarm=alarm):
            existing = self._entries.get(c.key)
            if existing is None:
                self._entries[c.key] = c
                added.append(c)
            else:
                existing.last_seen_round = attempt
                existing.times_seen += 1
        return added

    def snapshot(self) -> list[TypedConstraint]:
        return [replace(c) for c in self._entries.values()]


def render_prompt_block(entries: list[TypedConstraint]) -> str:
    if not entries:
        return ""
    lines = [
        "=== ACCUMULATED TYPED CONSTRAINTS (H_t) ===",
        "Each entry comes from a failed audit in an earlier attempt. The set only grows: every entry "
        "still applies in this attempt, so do NOT regress on any of them.",
        "",
    ]
    for c in entries:
        lines.append(f"- {constraint_tag(c)} {c.statement}")
        if c.evidence:
            lines.append("  evidence: " + _clip(c.evidence.replace("\n", " | "), _PROMPT_EVIDENCE_CAP))
    lines.append("=== END ACCUMULATED TYPED CONSTRAINTS ===")
    return "\n".join(lines)


def decision_payload(entries: list[TypedConstraint]) -> list[dict]:
    return [
        {
            "id": constraint_tag(c),
            "level": c.level,
            "statement": c.statement,
            "evidence": _clip(c.evidence.replace("\n", " | "), _PROMPT_EVIDENCE_CAP),
        }
        for c in entries
    ]


def contract_items(entries: list[TypedConstraint]) -> tuple[list[str], list[str]]:
    """(must_fix, must_not_touch) lines for the ledger entries. L1 is about patch format, not the contract."""
    must_fix: list[str] = []
    must_not_touch: list[str] = []
    for c in entries:
        if c.level == "L2":
            loc = f"{c.file}:{c.line}" if c.line else c.file
            must_fix.append(f"{constraint_tag(c)} {loc} — target alarm `{c.rule}` must be eliminated")
        elif c.level == "L3":
            must_not_touch.append(f"{constraint_tag(c)} {c.file} — must not introduce `{c.rule}`")
    return must_fix, must_not_touch


def merge_into_contract(contract: RepairContract, entries: list[TypedConstraint]) -> dict:
    """Union the ledger-derived items into the GW contract in place (never removes anything)."""
    must_fix, must_not_touch = contract_items(entries)
    added_fix = [i for i in must_fix if i not in contract.must_fix]
    added_mnt = [i for i in must_not_touch if i not in contract.must_not_touch]
    contract.must_fix.extend(added_fix)
    contract.must_not_touch.extend(added_mnt)
    return {
        "ledger_size": len(entries),
        "must_fix_added": len(added_fix),
        "must_not_touch_added": len(added_mnt),
    }
