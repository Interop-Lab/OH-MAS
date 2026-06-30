from __future__ import annotations

from oh_mas.agents.gw_tools.context import GWToolContext
from oh_mas.core.schemas import PreciseContextSlice, RepairContract


def set_must_fix(ctx: GWToolContext, *, items: list[str]) -> dict:
    """Declare what MUST be changed to fix the target alarm(s).

    Each item should reference a concrete file:line location or code pattern.
    In retry scenarios, include items for both the original alarm and any
    newly introduced alarms from the previous round.
    """
    for item in items:
        if item and item not in ctx.repair_contract.must_fix:
            ctx.repair_contract.must_fix.append(item)
    return ctx.record_step("set_must_fix", {"must_fix": ctx.repair_contract.must_fix}, items=items)


def set_must_not_touch(ctx: GWToolContext, *, items: list[str]) -> dict:
    """Declare what MUST NOT be modified to avoid regressions.

    Derived from repair experiences (known side-effects) and dependency graph callers.
    """
    for item in items:
        if item and item not in ctx.repair_contract.must_not_touch:
            ctx.repair_contract.must_not_touch.append(item)
    return ctx.record_step("set_must_not_touch", {"must_not_touch": ctx.repair_contract.must_not_touch}, items=items)


def set_allowed_transformations(ctx: GWToolContext, *, items: list[str]) -> dict:
    """Declare HOW the fix may be applied (concrete transformation options).

    Derived from rule_templates fixed_code patterns and repair experiences.
    """
    for item in items:
        if item and item not in ctx.repair_contract.allowed_transformations:
            ctx.repair_contract.allowed_transformations.append(item)
    return ctx.record_step(
        "set_allowed_transformations",
        {"allowed_transformations": ctx.repair_contract.allowed_transformations},
        items=items,
    )


def finalize_context(ctx: GWToolContext, *, reasoning: str, confidence: float) -> PreciseContextSlice:
    output = PreciseContextSlice(
        task_id=ctx.task_id,
        mode=ctx.mode,
        repair_contract=ctx.repair_contract,
        raw_graph_slice=ctx.graph_data,
        rule_context_mode=ctx.rule_mode.get("slice_strategy", ""),
        slicing_strategy="rule_aware",
        llm_reasoning=reasoning,
        agent_confidence=confidence,
    )
    ctx.record_step(
        "finalize_context",
        {
            "mode": output.slicing_strategy,
            "must_fix": len(output.repair_contract.must_fix),
            "must_not_touch": len(output.repair_contract.must_not_touch),
            "allowed_transformations": len(output.repair_contract.allowed_transformations),
            "confidence": confidence,
        },
        reasoning=reasoning,
        confidence=confidence,
    )
    return output
