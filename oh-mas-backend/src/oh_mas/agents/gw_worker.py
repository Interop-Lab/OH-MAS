from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
import urllib.error
import urllib.request

from oh_mas.agents.gw_tools import (
    GWToolContext,
    analyze_component,
    find_component_at_line,
    finalize_context,
    grep_file,
    grep_neighbors,
    list_neighbors,
    read_lines,
    read_symbol,
    run_arkts_generic_slicer,
    run_rule_slicer,
    set_allowed_transformations,
    set_must_fix,
    set_must_not_touch,
    show_alarm,
    show_graph_overview,
    show_knowledge,
    show_rule_mode,
    view_file_structure,
)
from oh_mas.core.schemas import GWProfileInput, KnowledgePack, PreciseContextSlice
from oh_mas.gw_context_lib import get_context_mode


@dataclass
class GWWorkerConfig:
    enable_llm: bool = True  # 默认启用 LLM 驱动
    llm_model: str = ""
    llm_model_class: str = "openrouter"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1200
    llm_timeout: int = 30
    max_steps: int = 15
    deterministic_fallback: bool = True
    llm_max_retries: int = 3  # LLM 请求重试次数
    llm_retry_delay: float = 1.0  # 重试间隔基数（秒），指数退避


@dataclass
class GWWorkerResult:
    precise_slice: PreciseContextSlice
    trace_steps: list[dict] = field(default_factory=list)
    llm_traces: list[dict] = field(default_factory=list)
    mode: str = "deterministic"


class GWWorker:
    """Deterministic GW worker that follows the future tool-driven agent path."""

    def __init__(self, config: GWWorkerConfig | None = None):
        self.config = config or GWWorkerConfig()

    def run(
        self,
        *,
        gw_input: GWProfileInput,
        graph_data: dict,
        knowledge_pack: KnowledgePack | None = None,
        repo_root: str = "",
        retry_index: int = 0,
        introduced_rules: list[str] | None = None,
    ) -> GWWorkerResult:
        rule_mode = get_context_mode(gw_input.alarm.rule)
        introduced_rules = introduced_rules or []

        previous_audit_feedback = getattr(gw_input, "previous_audit_feedback", None)

        if self.config.enable_llm and self.config.llm_model:
            # 尝试 LLM 驱动模式
            llm_ctx = GWToolContext(
                task_id=gw_input.task_id,
                mode=gw_input.mode,
                alarm=gw_input.alarm,
                graph_data=graph_data,
                rule_mode=rule_mode,
                knowledge_pack=knowledge_pack,
                repo_root=repo_root,
                phase="llm",
                retry_index=retry_index,
                introduced_rules=introduced_rules,
                previous_audit_feedback=previous_audit_feedback,
            )
            try:
                precise, llm_traces = self._run_llm_loop(llm_ctx)
                return GWWorkerResult(
                    precise_slice=precise,
                    trace_steps=list(llm_ctx.trace_steps),
                    llm_traces=llm_traces,
                    mode="llm",
                )
            except Exception as exc:
                # LLM 失败，记录错误
                llm_error_info = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "llm_steps_before_failure": len(llm_ctx.trace_steps),
                }
                if not self.config.deterministic_fallback:
                    raise

                # Fallback: 创建新的 context，不继承 LLM 阶段的收集数据
                fallback_ctx = GWToolContext(
                    task_id=gw_input.task_id,
                    mode=gw_input.mode,
                    alarm=gw_input.alarm,
                    graph_data=graph_data,
                    rule_mode=rule_mode,
                    knowledge_pack=knowledge_pack,
                    repo_root=repo_root,
                    phase="fallback",
                )
                # 记录 fallback 原因
                fallback_ctx.record_step("llm_fallback_triggered", {
                    "reason": "llm_execution_failed",
                    "llm_error": llm_error_info,
                    "fallback_to": "deterministic_policy",
                })
                precise = self._run_deterministic_policy(fallback_ctx)
                return GWWorkerResult(
                    precise_slice=precise,
                    trace_steps=list(fallback_ctx.trace_steps),
                    llm_traces=[],  # LLM 失败，不返回其 traces
                    mode="fallback",
                )

        # 直接使用确定性策略（LLM 未启用或无模型配置）
        ctx = GWToolContext(
            task_id=gw_input.task_id,
            mode=gw_input.mode,
            alarm=gw_input.alarm,
            graph_data=graph_data,
            rule_mode=rule_mode,
            knowledge_pack=knowledge_pack,
            repo_root=repo_root,
            phase="deterministic",
            retry_index=retry_index,
            introduced_rules=introduced_rules,
            previous_audit_feedback=previous_audit_feedback,
        )
        precise = self._run_deterministic_policy(ctx)
        return GWWorkerResult(precise_slice=precise, trace_steps=list(ctx.trace_steps), mode="deterministic")

    def _run_deterministic_policy(self, ctx: GWToolContext) -> PreciseContextSlice:
        # Deterministic fallback: LLM unavailable, emit empty contract.
        show_alarm(ctx)
        show_knowledge(ctx)
        return finalize_context(
            ctx,
            reasoning="Deterministic fallback: LLM unavailable, repair contract not synthesized.",
            confidence=0.0,
        )

    def _run_llm_loop(self, ctx: GWToolContext) -> tuple[PreciseContextSlice, list[dict]]:
        messages = self._initial_messages(ctx)
        llm_traces: list[dict] = []
        finalized: PreciseContextSlice | None = None

        for step_idx in range(1, max(1, self.config.max_steps) + 1):
            request_params = self._request_params(step_idx=step_idx)
            start = perf_counter()
            response = self._query_llm(messages=messages, request_params=request_params)
            duration_ms = round((perf_counter() - start) * 1000, 3)
            raw_content = _extract_response_content(response)
            action, parse_status, parse_error = _parse_action(raw_content)
            llm_traces.append(
                {
                    "agent": "GW",
                    "worker_id": "gw",
                    "provider": request_params["model_class"],
                    "model_id": request_params["model_name"],
                    "request_params": request_params,
                    "messages": list(messages),
                    "response": response,
                    "action": action,
                    "parse_status": parse_status,
                    "parse_error": parse_error,
                    "duration_ms": duration_ms,
                }
            )
            messages.append({"role": "assistant", "content": raw_content})
            if parse_status != "ok":
                messages.append({"role": "user", "content": f"Invalid action JSON: {parse_error}. Return one valid JSON action."})
                continue

            tool_name = str(action.get("tool") or "")
            args = action.get("args") if isinstance(action.get("args"), dict) else {}
            try:
                output = self._execute_tool(ctx, tool_name, args)
            except Exception as exc:
                output = ctx.record_step(
                    "tool_error",
                    {
                        "tool": tool_name,
                        "args": args,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    },
                )
            if isinstance(output, PreciseContextSlice):
                finalized = output
                break
            messages.append({"role": "user", "content": json.dumps({"tool": tool_name, "output": output}, ensure_ascii=False)})

        if finalized is None:
            finalized = finalize_context(
                ctx,
                reasoning="GW LLM tool loop reached step limit; finalized with collected tool state.",
                confidence=0.62,
            )
        return finalized, llm_traces

    @staticmethod
    def _format_rule_specific_section(alarm_rule: str) -> str:
        """Build rule-specific protocol section for systemic C++ patterns.

        For cppcheck rules where the same pattern appears 10-30 times in a single
        file, the default GW flow (read alarm location → write contract) is
        insufficient: must_fix ends up covering only the alarmed line, leaving the
        rest unfixed and causing DA L2 failure on every attempt.

        This section is injected into the GW system prompt ONLY for these rules,
        giving GW explicit grep-first, enumerate-all, verify-zero instructions.
        """
        SYSTEMIC_RULES = {
            "cppcheck/knownConditionTrueFalse",
            "cppcheck/passedByValue",
            "cppcheck/variableScope",
            "cppcheck/useStlAlgorithm",
            "cppcheck/stlIfStrFind",
            "cppcheck/memleak",
        }
        if alarm_rule not in SYSTEMIC_RULES:
            return ""

        # Per-rule: grep pattern(s) and must_fix item template
        RULE_DETAILS: dict[str, dict] = {
            "cppcheck/knownConditionTrueFalse": {
                "summary": "redundant always-true/false condition (null-check or unsigned >= 0)",
                "grep_advice": (
                    'Extract the variable name from the alarm message, then:\n'
                    '  grep_file(file=alarm_file, pattern=r"if \\(VARNAME != nullptr\\)", max_matches=50)\n'
                    '  grep_file(file=alarm_file, pattern=r"if \\(VARNAME != NULL\\)", max_matches=50)\n'
                    "Replace VARNAME with the actual variable. Also search for the condition pattern\n"
                    "from the alarm message directly."
                ),
                "must_fix_template": (
                    '"<file>:<line> — if (VARNAME != nullptr) is always true here; '
                    "remove the guard and keep the body unconditionally"
                    '"  ← one entry per matching line'
                ),
                "allowed_tx": (
                    "Remove the `if (ptr != nullptr)` guard at EVERY matched line; "
                    "keep the body (delete/free/cleanup) executing unconditionally. "
                    "Safe because cppcheck's dataflow analysis proves the pointer is "
                    "always non-null at those points."
                ),
            },
            "cppcheck/passedByValue": {
                "summary": "expensive copy of std::string / std::vector / std::map parameter",
                "grep_advice": (
                    "  grep_file(file=alarm_file, pattern=r'std::string [a-zA-Z]', max_matches=50)\n"
                    "  grep_file(file=alarm_file, pattern=r'std::vector<', max_matches=50)\n"
                    "  grep_file(file=alarm_file, pattern=r'std::map<', max_matches=50)\n"
                    "Also grep the corresponding header (.h) for declarations."
                ),
                "must_fix_template": (
                    '"<file>:<line> — parameter `NAME` (std::TYPE) should be const TYPE&; '
                    "change both declaration and definition"
                    '"  ← one entry per parameter'
                ),
                "allowed_tx": (
                    "Change `std::string NAME` → `const std::string& NAME` (likewise for "
                    "vector/map). Apply to BOTH the .h declaration and .cpp definition. "
                    "Skip parameters that are modified inside the function body or moved."
                ),
            },
            "cppcheck/variableScope": {
                "summary": "variable declared at function top but only used in an inner block",
                "grep_advice": (
                    "Read the full alarm file with read_lines(file, 1, <large_N>). "
                    "For each function that contains the alarm line, scan all variable "
                    "declarations at function scope and check whether the variable is used "
                    "only in one nested block (if/for/while)."
                ),
                "must_fix_template": (
                    '"<file>:<line> — variable `NAME` declared at function scope but only '
                    "used inside the following if/for block; move declaration to first use"
                    '"  ← one entry per variable'
                ),
                "allowed_tx": (
                    "Move variable declaration from function top to just before its first use, "
                    "or into the innermost block that contains all its uses. "
                    "For C89-style files (declarations-at-top), check if C99 inline "
                    "declarations are allowed before changing style."
                ),
            },
            "cppcheck/useStlAlgorithm": {
                "summary": "raw loop replaceable by a standard STL algorithm",
                "grep_advice": (
                    "  grep_file(file=alarm_file, pattern=r'for.*push_back', max_matches=50)\n"
                    "  grep_file(file=alarm_file, pattern=r'\\.begin\\(\\).*\\.end\\(\\)', max_matches=50)\n"
                    "  grep_file(file=alarm_file, pattern=r'for.*\\.size\\(\\)', max_matches=50)"
                ),
                "must_fix_template": (
                    '"<file>:<line> — raw for-loop over container; replace with '
                    "std::transform / std::find_if / std::count_if / std::all_of"
                    '"  ← one entry per loop'
                ),
                "allowed_tx": (
                    "Replace raw loops with the matching STL algorithm: "
                    "push_back loops → std::transform + std::back_inserter; "
                    "search-and-break → std::find_if; counting loops → std::count_if; "
                    "all-satisfy loops → std::all_of. "
                    "Add #include <algorithm> (and <numeric> for accumulate) if absent."
                ),
            },
            "cppcheck/stlIfStrFind": {
                "summary": "string::find() used in a condition where starts_with/ends_with is clearer",
                "grep_advice": (
                    "  grep_file(file=alarm_file, pattern=r'\\.find\\(', max_matches=50)\n"
                    "Then filter hits for patterns: `== 0`, `== size()`, `>= 0`, `!= 0` in the same line."
                ),
                "must_fix_template": (
                    '"<file>:<line> — `str.find(X) == 0` should be `str.starts_with(X)` (C++20); '
                    "or `str.rfind(X) == str.size()-N` should be `str.ends_with(X)`"
                    '"  ← one entry per occurrence'
                ),
                "allowed_tx": (
                    "Replace `find(x) == 0` with `starts_with(x)` and "
                    "`rfind(x) == size-N` with `ends_with(x)` (requires C++20). "
                    "Do NOT change `find(x) != npos` — that is a different pattern."
                ),
            },
            "cppcheck/memleak": {
                "summary": "heap allocation leaks on some exit paths",
                "grep_advice": (
                    "  grep_file(file=alarm_file, pattern=r'\\bnew\\b', max_matches=50)\n"
                    "  grep_file(file=alarm_file, pattern=r'malloc\\(|calloc\\(|realloc\\(', max_matches=50)\n"
                    "For EACH allocation site, read the enclosing function and trace "
                    "every return / goto / early-exit path."
                ),
                "must_fix_template": (
                    '"<file>:<line> — early return / error path does not free `NAME` allocated at line N; '
                    "add delete/free before this return"
                    '"  ← one entry per leaking exit path'
                ),
                "allowed_tx": (
                    "Add `free(ptr)` / `delete ptr` on every exit path that currently skips it. "
                    "Also check for pointer reassignment without freeing the old value first. "
                    "Consider RAII (std::unique_ptr) if the function is complex."
                ),
            },
        }

        details = RULE_DETAILS[alarm_rule]
        return (
            f"\n## ⚠️ SYSTEMIC PATTERN PROTOCOL — `{alarm_rule}`\n\n"
            f"**Pattern type:** {details['summary']}\n\n"
            "This rule flags a **file-level pattern**: the alarm line is the *first* instance\n"
            "cppcheck detected, but the same file typically has **10–30 more occurrences**.\n"
            "If must_fix covers only the alarm line, DA L2 will fail because cppcheck will\n"
            "report the remaining unfixed instances at different line numbers.\n\n"
            "### Mandatory workflow for this rule\n\n"
            "**Step 1 — grep BEFORE writing must_fix**\n"
            f"{details['grep_advice']}\n\n"
            "Use `max_matches=50` to avoid truncation on large files (default is 20).\n\n"
            "**Step 2 — must_fix: one entry per instance**\n"
            f"Template: {details['must_fix_template']}\n\n"
            "Every line returned by grep_file that matches the pattern MUST appear in\n"
            "must_fix. Do NOT summarise as 'fix all occurrences' — CP cannot act on vague items.\n\n"
            "**Step 3 — allowed_transformations**\n"
            f"{details['allowed_tx']}\n\n"
            "**Step 4 — verify**\n"
            "After setting must_fix, call grep_file once more with the same pattern and confirm\n"
            "that every returned line number is already listed in must_fix. If any are missing,\n"
            "call set_must_fix again with the complete list before finalize_context.\n"
        )

    @staticmethod
    def _format_previous_failure_section(ctx: GWToolContext) -> str:
        """Build a structured failure-context section for the GW system prompt.

        Converts the previous_audit_feedback into actionable contract guidance so
        GW can reason about what went wrong and avoid repeating the same mistakes.
        """
        fb = ctx.previous_audit_feedback
        if fb is None:
            return ""

        lines: list[str] = [
            f"\n## ⚠️ PREVIOUS ATTEMPT FAILURE (retry_index={ctx.retry_index})",
            f"**Failed at:** {fb.failed_level}  |  **Reason:** {fb.reason}",
            "",
        ]

        if fb.failed_level == "L2":
            lines.append("### Residual Alarm Locations (linter still detected the target rule here)")
            lines.append("Your new contract's must_fix MUST cover ALL of these locations:")
            lines.append("")
            seen_locations: set[str] = set()
            for diag in fb.patch_diagnostics:
                if diag.failed_level == "L2":
                    for v in diag.linter_violations:
                        loc = f"{v.file}:{v.line}"
                        if loc not in seen_locations:
                            seen_locations.add(loc)
                            lines.append(f"- `{loc}` — {v.message}")
                            if v.code_snippet:
                                lines.append("  ```")
                                for cl in v.code_snippet.splitlines():
                                    lines.append(f"  {cl}")
                                lines.append("  ```")
            lines.append("")
            lines.append("**Contract guidance for L2 failure:**")
            _SYSTEMIC_CPP = {
                "cppcheck/knownConditionTrueFalse", "cppcheck/passedByValue",
                "cppcheck/variableScope", "cppcheck/useStlAlgorithm",
                "cppcheck/stlIfStrFind", "cppcheck/memleak",
            }
            if fb_rule := getattr(ctx.alarm, "rule", ""):
                if fb_rule in _SYSTEMIC_CPP:
                    lines.append("- The residual locations above are ADDITIONAL instances of the same file-level pattern.")
                    lines.append("- Use grep_file (max_matches=50) on the alarm file to enumerate ALL remaining instances.")
                    lines.append("- must_fix MUST list every line returned by grep_file — not just the residual lines above.")
                    lines.append("- The SYSTEMIC PATTERN PROTOCOL section above specifies the exact grep patterns to use.")
                else:
                    lines.append("- Each residual alarm line above is a SEPARATE callsite that needs its own fix.")
                    lines.append("- Scan the ENTIRE alarm file for all occurrences — the previous contract likely missed some.")
                    lines.append("- must_fix must enumerate EVERY occurrence in the file, not just the alarm line.")

        elif fb.failed_level == "L3":
            intro_list = "\n".join(f"  - {r}" for r in ctx.introduced_rules) if ctx.introduced_rules else "  (none)"
            lines.append(f"### Newly Introduced Rules (previous patch side-effects):\n{intro_list}")
            lines.append("")
            lines.append("### Introduced Warning Details:")
            seen_rules: set[str] = set()
            for diag in fb.patch_diagnostics:
                if diag.failed_level == "L3":
                    for w in diag.introduced_warnings:
                        if w.rule not in seen_rules:
                            seen_rules.add(w.rule)
                            lines.append(f"- **`{w.rule}`** at `{w.repo_relative_file or w.file}:{w.line}`")
                            if w.code_snippet:
                                lines.append("  ```")
                                for cl in w.code_snippet.splitlines():
                                    lines.append(f"  {cl}")
                                lines.append("  ```")
                            if w.message:
                                lines.append(f"  *{w.message}*")
            lines.append("")
            lines.append("**Contract guidance for L3 failure:**")
            lines.append("- must_fix must now include items for BOTH the original alarm AND each introduced rule above.")
            lines.append("- must_not_touch should be stricter — previous patch already caused regressions.")
            lines.append("- allowed_transformations must include patterns from knowledge_pack for each introduced rule.")
            lines.append("- For `suggest-reuseid-for-if-else-reusable-component`: eliminate ALL if-else from @Reusable")
            lines.append("  struct methods (including @Builder) by replacing with .visibility() ternary.")
            lines.append("- For `replace-nested-reusable-component-by-builder`: inline nested custom components")
            lines.append("  completely into @Builder methods; @Builder must contain ONLY built-in UI components.")

        return "\n".join(lines)

    def _initial_messages(self, ctx: GWToolContext) -> list[dict[str, str]]:
        max_steps = self.config.max_steps
        is_retry = ctx.retry_index > 0

        retry_section = ""
        if is_retry:
            intro_list = "\n".join(f"  - {r}" for r in ctx.introduced_rules) if ctx.introduced_rules else "  (none)"
            retry_section = (
                f"\n## \u26a0\ufe0f RETRY CONTEXT (retry_index={ctx.retry_index})\n"
                "This is a re-attempt. Your repair contract MUST address ALL of:\n\n"
                f"1. **Original alarm** (always): `{ctx.alarm.rule}` at `{ctx.alarm.file}`:{ctx.alarm.line_start}\n"
                f"2. **Newly introduced rules** (from previous patch side-effects):\n{intro_list}\n\n"
                "- must_fix: include items for BOTH the original alarm AND each introduced warning\n"
                "- must_not_touch: be stricter \u2014 previous patch already caused regressions\n"
                "- allowed_transformations: include patterns from knowledge_pack for introduced rules\n"
            )
            retry_section += self._format_previous_failure_section(ctx)

        rule_specific_section = self._format_rule_specific_section(ctx.alarm.rule)

        system_prompt = (
            "You are GW (Graph Weaver), a contract synthesis agent in OH-MAS (OpenHarmony Multi-Agent System).\n"
            + retry_section
            + rule_specific_section +
            "\n## Your PRIMARY Mission: Repair Contract Synthesis\n"
            "Your user message contains three pre-loaded inputs:\n\n"
            "- **alarm** — where to start: `file` + `line_start` is the exact location to read first.\n"
            "- **rule_mode** — what to look for: `description` explains the full fix semantics and cascade traps;\n"
            "  `must_include` lists the code elements your contract MUST cover;\n"
            "  `snippet_hints` tells you which code blocks to read;\n"
            "  `slice_strategy` tells you whether cross-file graph analysis is needed.\n"
            "- **knowledge_pack** — how to fix: `rule_templates` give concrete buggy→fixed patterns to base\n"
            "  `allowed_transformations` on; `experiences` warn about known regression risks that must inform\n"
            "  `must_not_touch`.\n\n"
            "Read `rule_mode.description` and `knowledge_pack` first, then navigate to the alarm location and\n"
            "verify each item in `rule_mode.must_include` against actual code before writing the contract.\n\n"
            f"## Step Limit\n\u26a0\ufe0f Maximum {max_steps} steps.\n"
            "Flow: 2-3 steps reading code \u2192 1 step building contract \u2192 1 step finalize\n\n"
            "## Available Tools\n\n"
            "### Phase 1 \u2014 Code Verification:\n"
            "- show_graph_overview, list_neighbors, analyze_component, find_component_at_line\n"
            "- view_file_structure, read_lines, read_symbol, grep_file, grep_neighbors\n\n"
            "### Phase 2 \u2014 Contract Writing:\n"
            '- set_must_fix(items): items = ["<file>:<line> \u2014 <pattern and why>", ...]\n'
            '- set_must_not_touch(items): items = ["<file>:<symbol> \u2014 <why>", ...]\n'
            '- set_allowed_transformations(items): items = [concrete pattern from templates/experiences, ...]\n\n'
            "### Phase 3 \u2014 Finalization:\n"
            "- finalize_context(reasoning, confidence)\n\n"
            "## Response Format\n"
            'Return exactly ONE JSON object per turn: {"tool": "tool_name", "args": {...}}\n\n'
            "## Quality Criteria\n"
            "\u2705 GOOD must_fix: \"Index.ets:42 \u2014 LazyForEach iterates VideoItem but VideoItemComponent lacks @Reusable\"\n"
            "\u274c BAD must_fix: \"fix the alarm\"\n\n"
            "\u2705 GOOD allowed_transformations: \"Add @Reusable to VideoItemComponent; add aboutToReuse(params) to reset @State props\"\n"
            "\u274c BAD allowed_transformations: \"use @Reusable\"\n\n"
            "## Anti-Patterns\n"
            "- \u274c Starting with graph exploration before reading alarm location code\n"
            "- \u274c Writing vague contract items without file:line evidence\n"
            "- \u274c Skipping set_must_not_touch when experiences mention regression risks\n"
            "- \u274c Forgetting introduced_rules in retry scenarios\n"
        )

        pack = ctx.knowledge_pack

        # Pre-execute info tools and record steps so trace stays complete
        alarm_data = show_alarm(ctx)
        knowledge_data = show_knowledge(ctx)
        rule_mode_data = show_rule_mode(ctx)

        payload: dict = {
            "alarm": alarm_data,
            "rule_mode": rule_mode_data,
            "knowledge_pack": knowledge_data,
            "mode": ctx.mode,
            "retry_index": ctx.retry_index,
            "introduced_rules": ctx.introduced_rules,
            "previous_audit_feedback": (
                ctx.previous_audit_feedback.to_dict()
                if ctx.previous_audit_feedback is not None else None
            ),
            "graph_meta": ctx.graph_data.get("meta", {}),
            "repo_root_available": bool(ctx.repo_root),
            "step_limit": max_steps,
        }

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    def _request_params(self, *, step_idx: int) -> dict[str, Any]:
        return {
            "model_class": self.config.llm_model_class,
            "model_name": self.config.llm_model,
            "temperature": self.config.llm_temperature,
            "max_tokens": self.config.llm_max_tokens,
            "timeout": self.config.llm_timeout,
            "step_idx": step_idx,
        }

    def _query_llm(self, *, messages: list[dict[str, str]], request_params: dict[str, Any]) -> dict[str, Any]:
        """Query LLM with retry mechanism for transient errors."""
        model_class = str(request_params.get("model_class") or "openrouter").lower()
        max_retries = self.config.llm_max_retries
        retry_delay = self.config.llm_retry_delay
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                if model_class == "openrouter":
                    return self._query_openrouter(messages=messages, request_params=request_params)
                raise RuntimeError(f"Unsupported GW model_class: {model_class}")
            except RuntimeError as exc:
                last_error = exc
                error_msg = str(exc).lower()
                # 判断是否为可重试的临时性错误
                is_retryable = any(keyword in error_msg for keyword in [
                    "url_error", "ssl", "timeout", "connection", "eof", "reset",
                    "http_502", "http_503", "http_504", "http_429",
                ])
                if is_retryable and attempt < max_retries - 1:
                    sleep_time = retry_delay * (2 ** attempt)  # 指数退避
                    time.sleep(sleep_time)
                    continue
                raise

        # 不应该到达这里，但作为安全措施
        if last_error:
            raise last_error
        raise RuntimeError("LLM query failed with unknown error")

    def _query_openrouter(self, *, messages: list[dict[str, str]], request_params: dict[str, Any]) -> dict[str, Any]:
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        model_name = str(request_params.get("model_name") or "").strip()
        if not model_name:
            raise RuntimeError("GW llm_model is empty")
        resolved_model = model_name.split("/", 1)[1] if model_name.startswith("openrouter/") else model_name
        payload = {
            "model": resolved_model,
            "messages": messages,
            "temperature": float(request_params.get("temperature", 0.0)),
            "max_completion_tokens": int(request_params.get("max_tokens", 1200)),
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=int(request_params.get("timeout", 30))) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"openrouter_http_{exc.code}: {error_body}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"openrouter_url_error: {exc}") from None

        raw = json.loads(body)
        choice = ((raw.get("choices") or [{}])[0] or {}).get("message") or {}
        return {"role": str(choice.get("role") or "assistant"), "content": choice.get("content") or "", "extra": {"response": raw}}

    def _execute_tool(self, ctx: GWToolContext, tool_name: str, args: dict) -> Any:
        if tool_name == "show_alarm":
            return show_alarm(ctx)
        if tool_name == "show_rule_mode":
            return show_rule_mode(ctx)
        if tool_name == "show_knowledge":
            return show_knowledge(ctx)
        if tool_name == "show_graph_overview":
            return show_graph_overview(ctx)
        if tool_name == "list_neighbors":
            return list_neighbors(ctx, **_pick(args, {"max_hops", "file_pattern", "include_external"}))
        if tool_name == "view_file_structure":
            return view_file_structure(ctx, **_pick(args, {"file", "mode", "focus_symbols"}))
        if tool_name == "read_lines":
            # Alias start/end → start_line/end_line for LLM typo tolerance
            if "start" in args and "start_line" not in args:
                args["start_line"] = args.pop("start")
            if "end" in args and "end_line" not in args:
                args["end_line"] = args.pop("end")
            return read_lines(ctx, **_pick(args, {"file", "start_line", "end_line", "context_before", "context_after"}))
        if tool_name == "read_symbol":
            return read_symbol(ctx, **_pick(args, {"file", "symbol", "symbol_type", "include_decorators", "include_class_header", "max_lines"}))
        if tool_name == "grep_file":
            return grep_file(ctx, **_pick(args, {"file", "pattern", "max_matches", "context_lines"}))
        if tool_name == "grep_neighbors":
            return grep_neighbors(ctx, **_pick(args, {"pattern", "max_hops", "file_pattern", "max_files", "max_matches_per_file"}))
        if tool_name == "set_must_fix":
            raw = args.get("items", [])
            items = raw if isinstance(raw, list) else [str(raw)]
            return set_must_fix(ctx, items=[str(i) for i in items if i])
        if tool_name == "set_must_not_touch":
            raw = args.get("items", [])
            items = raw if isinstance(raw, list) else [str(raw)]
            return set_must_not_touch(ctx, items=[str(i) for i in items if i])
        if tool_name == "set_allowed_transformations":
            raw = args.get("items", [])
            items = raw if isinstance(raw, list) else [str(raw)]
            return set_allowed_transformations(ctx, items=[str(i) for i in items if i])
        if tool_name == "analyze_component":
            return analyze_component(ctx, **_pick(args, {"file", "component_name"}))
        if tool_name == "find_component_at_line":
            return find_component_at_line(ctx, **_pick(args, {"file", "line"}))
        if tool_name == "finalize_context":
            return finalize_context(
                ctx,
                reasoning=str(args.get("reasoning") or "GW LLM finalized precise context."),
                confidence=float(args.get("confidence", 0.7)),
            )
        return ctx.record_step("unknown_tool", {"tool": tool_name, "args": args, "error": "unknown_tool"})




def _extract_response_content(response: dict[str, Any]) -> str:
    content = response.get("content")
    if isinstance(content, str):
        return content.strip()
    return ""


def _parse_action(content: str) -> tuple[dict[str, Any], str, str]:
    if not content:
        return {}, "empty", "empty response"
    raw = content.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        if lines and lines[0].strip().lower() == "json":
            lines = lines[1:]
        raw = "\n".join(lines).strip()
    try:
        action = json.loads(raw)
    except Exception as exc:
        return {}, "invalid_json", str(exc)
    if not isinstance(action, dict):
        return {}, "invalid_shape", "action is not an object"
    if not isinstance(action.get("tool"), str):
        return {}, "invalid_shape", "tool must be a string"
    if "args" in action and not isinstance(action["args"], dict):
        return {}, "invalid_shape", "args must be an object"
    action.setdefault("args", {})
    return action, "ok", ""


def _pick(args: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if key in allowed}
