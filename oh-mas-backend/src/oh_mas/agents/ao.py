from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from oh_mas.core.schemas import (
    Alarm,
    AuditDoneFailedEvent,
    CPInput,
    GWInput,
    KnowledgePack,
    Mode,
    PreviousAuditFeedback,
    PromptInjection,
    TaskProfiledEvent,
)
from oh_mas.core.tracing import to_jsonable, utc_now_iso
from oh_mas.oh_kb.client import OHKBClient


@dataclass
class AOConfig:
    mode_models: dict[str, list[str]]
    models_registry: list[str] = field(default_factory=list)
    enable_llm_decision: bool = False
    llm_model: str = ""
    llm_model_class: str = "litellm"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 800
    llm_timeout: int = 30
    kb_max_items: int = 8
    kb_timeout_ms: int = 300
    fail_open: bool = True


class AOAgent:
    MODE_ORDER: list[Mode] = ["easy", "medium", "hard"]

    def __init__(self, kb_client: OHKBClient, config: AOConfig):
        self.kb_client = kb_client
        self.config = config
        self.last_debug: dict[str, Any] = {}
        self.last_llm_traces: list[dict[str, Any]] = []

    def mode_for_retry(self, retry_index: int) -> Mode:
        idx = min(max(retry_index, 0), 2)
        return self.MODE_ORDER[idx]

    def build_profiled_event(
        self,
        *,
        task_id: str,
        retry_index: int,
        alarm: Alarm,
        previous_audit: AuditDoneFailedEvent | None = None,
        previous_task_append: str = "",
    ) -> TaskProfiledEvent:
        self.last_debug = {}
        self.last_llm_traces = []
        mode = self.mode_for_retry(retry_index)
        language = self._infer_language(alarm.file)
        query_common = {
            "rule_id": alarm.rule,
            "rule": alarm.rule,
            "language": language,
            "max_items": self.config.kb_max_items,
            "request_id": f"{task_id}:ao:{retry_index}",
            "task_id": task_id,
            "retry_index": retry_index,
            "agent_name": "AO",
            "timeout_ms": self.config.kb_timeout_ms,
            "fail_open": self.config.fail_open,
        }
        fw = self.kb_client.query_framework_knowledge(**query_common)
        rules = self.kb_client.query_rule_templates(**query_common)
        exp = self.kb_client.query_repair_experience(**query_common)

        kb_results = self._build_kb_results(
            alarm=alarm,
            framework=fw.to_dict(),
            rule_templates=rules.to_dict(),
            experiences=exp.to_dict(),
        )

        default_build_graph = mode in {"medium", "hard"}
        default_gw_input = GWInput(
            build_semantic_graph=default_build_graph,
            extract_constraints=default_build_graph,
        )
        allowed_models = self._allowed_models_for_mode(mode)
        default_models = self._default_models_for_mode(mode, allowed_models)
        previous_feedback = self._build_previous_feedback(previous_audit)

        # L3 failure: Query experiences for newly introduced rules
        extra_experiences = []
        if previous_feedback and previous_feedback.failed_level == "L3":
            introduced_rules = set()
            for diag in previous_feedback.patch_diagnostics:
                if diag.failed_level == "L3" and diag.introduced_warnings:
                    for warning in diag.introduced_warnings:
                        if warning.rule:
                            introduced_rules.add(warning.rule)

            # Query experiences for each introduced rule (limit to avoid overload)
            for introduced_rule in introduced_rules:
                intro_exp = self.kb_client.query_repair_experience(
                    rule_id=introduced_rule,
                    rule=introduced_rule,
                    language=language,
                    max_items=3,  # Limit per introduced rule
                    request_id=f"{task_id}:ao:{retry_index}:intro:{introduced_rule}",
                    task_id=task_id,
                    retry_index=retry_index,
                    agent_name="AO",
                    timeout_ms=self.config.kb_timeout_ms,
                    fail_open=self.config.fail_open,
                )
                extra_experiences.extend(intro_exp.to_dict().get("items", []))

        # Pack full text content for CP Agent (Layer 2 + Layer 3)
        # For L3 failures, include experiences for both original alarm and introduced warnings
        all_experiences = kb_results["experiences"].get("items", []) + extra_experiences
        knowledge_pack = KnowledgePack(
            rule_templates=kb_results["rule_templates"].get("items", []),
            experiences=all_experiences,
        )

        decision = self._decide_with_llm(
            task_id=task_id,
            retry_index=retry_index,
            mode=mode,
            alarm=alarm,
            language=language,
            previous_feedback=previous_feedback,
            previous_audit=previous_audit,
            allowed_models=allowed_models,
            default_models=default_models,
            default_gw_input=default_gw_input,
        )
        gw_input = self._resolve_gw_input(decision, default_gw_input)
        models = self._resolve_cp_models(decision, allowed_models, default_models)

        # Extract task_append from AO decision LLM (primary source)
        llm_task_append = ""
        cp_strategy = decision.get("cp_strategy")
        if isinstance(cp_strategy, dict) and cp_strategy.get("task_append"):
            llm_task_append = str(cp_strategy.get("task_append"))
        elif decision.get("task_append"):
            llm_task_append = str(decision.get("task_append"))

        # Build final task_append
        task_append = [f"Target rule: {alarm.rule}"]

        if previous_feedback is not None:
            # Fallback: If AO LLM didn't generate constraints, use basic template
            if not llm_task_append or "MANDATORY CONSTRAINTS" not in llm_task_append:
                fallback_constraints = self._build_fallback_constraints(previous_feedback, alarm)
                if fallback_constraints:
                    task_append.append(fallback_constraints)
            else:
                # Use AO LLM's generated constraints (includes analysis + code snippets)
                task_append.append(llm_task_append)

            # Accumulate: append constraints from the previous round so CP retains
            # lessons learned across multiple retry cycles (e.g., "must inline nested
            # components" survives into the round that focuses on "add reuseId").
            if previous_task_append:
                accumulated = self._extract_accumulated_constraints(previous_task_append)
                if accumulated:
                    task_append.append(accumulated)
        elif llm_task_append:
            # First attempt but LLM provided guidance
            task_append.append(llm_task_append)

        cp_input = CPInput(
            model_count=len(models),
            models=models,
            prompt_injection=PromptInjection(task_append="\n".join(task_append)),
            knowledge_pack=knowledge_pack,
        )

        self.last_debug = {
            "task_id": task_id,
            "retry_index": retry_index,
            "mode": mode,
            "language": language,
            "kb_queries": query_common,
            "kb_results": {
                "framework": kb_results["framework"],
                "rule_templates": kb_results["rule_templates"],
                "experiences": kb_results["experiences"],
            },
            "decision": decision,
            "gw_input": gw_input.to_dict(),
            "allowed_models": allowed_models,
            "default_models": default_models,
            "models": models,
        }

        return TaskProfiledEvent(
            task_id=task_id,
            retry_index=retry_index,
            mode=mode,
            alarm=alarm,
            previous_audit_feedback=previous_feedback,
            gw_input=gw_input,
            cp_input=cp_input,
        )

    @staticmethod
    def _build_previous_feedback(previous_audit: AuditDoneFailedEvent | None) -> PreviousAuditFeedback | None:
        if previous_audit is None:
            return None
        return PreviousAuditFeedback(
            failed_level=previous_audit.failed_level,
            reason=previous_audit.reason,
            failed_patches=list(previous_audit.failed_patches),
            patch_diagnostics=list(previous_audit.patch_diagnostics),
        )

    @staticmethod
    def _build_fallback_constraints(
        previous_feedback: PreviousAuditFeedback,
        alarm: Alarm
    ) -> str:
        """Fallback: Generate basic constraints when AO LLM is disabled or fails.

        This method only displays raw data (violations, code snippets) without
        deep analysis. When AO LLM is enabled, it should generate richer
        constraints with root cause analysis and specific guidance.
        """
        if previous_feedback.failed_level not in {"L2", "L3"}:
            return ""

        lines = [
            "=" * 60,
            "===  MANDATORY CONSTRAINTS  ===",
            "=" * 60,
            "",
            f"**Failure Type:** {previous_feedback.failed_level}",
            f"**Reason:** {previous_feedback.reason}",
            "",
        ]

        # L2: Target alarm not fixed - show linter violations
        if previous_feedback.failed_level == "L2":
            lines.append(f"The target alarm '{alarm.rule}' still exists at these locations:")
            lines.append("")

            for diag in previous_feedback.patch_diagnostics:
                if diag.failed_level == "L2" and diag.linter_violations:
                    lines.append(f"**From patch {diag.patch_id}:**")
                    for violation in diag.linter_violations:
                        location = f"- Line {violation.line}"
                        if violation.column:
                            location += f", Column {violation.column}"
                        lines.append(location)

                        if violation.code_snippet:
                            lines.append("  ```typescript")
                            lines.append(f"  {violation.code_snippet}")
                            lines.append("  ```")
                        lines.append("")

            lines.append("**Required Action:** Fix the target alarm at the exact locations listed above.")

        # L3: Introduced new warnings - show introduced warnings
        elif previous_feedback.failed_level == "L3":
            lines.append("Previous patches introduced new warnings:")
            lines.append("")

            # Group by rule
            warnings_by_rule: dict[str, list] = {}
            for diag in previous_feedback.patch_diagnostics:
                if diag.failed_level == "L3" and diag.introduced_warnings:
                    for warning in diag.introduced_warnings:
                        rule = warning.rule or "unknown"
                        if rule not in warnings_by_rule:
                            warnings_by_rule[rule] = []
                        warnings_by_rule[rule].append((diag.patch_id, warning))

            for rule, warnings in warnings_by_rule.items():
                lines.append(f"### Rule: `{rule}`")
                lines.append("")

                for patch_id, warning in warnings:
                    file_display = warning.repo_relative_file or warning.file
                    lines.append(f"**{file_display}:{warning.line}** (from {patch_id})")

                    if warning.code_snippet:
                        lines.append("```typescript")
                        lines.append(warning.code_snippet)
                        lines.append("```")

                    if warning.message:
                        lines.append(f"*Message:* {warning.message}")

                    lines.append("")

            lines.append("**Required Action:** Address ALL introduced warnings listed above.")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)

    @staticmethod
    def _extract_accumulated_constraints(previous_task_append: str) -> str:
        """Extract the MANDATORY CONSTRAINTS block from a previous task_append string.

        Wraps it in a clearly labelled "PREVIOUS ROUND CONSTRAINTS" section so CP
        workers understand these are lessons from an earlier attempt that must not be
        forgotten while addressing new (current-round) guidance.
        """
        if not previous_task_append:
            return ""
        # Only carry forward the MANDATORY CONSTRAINTS block, not the "Target rule:" line
        marker = "=== MANDATORY CONSTRAINTS ==="
        idx = previous_task_append.find(marker)
        if idx == -1:
            return ""
        constraints_block = previous_task_append[idx:].strip()
        if not constraints_block:
            return ""
        return (
            "=== CONSTRAINTS FROM PREVIOUS ROUND (still apply — do NOT regress) ===\n"
            + constraints_block
        )

    def _decide_with_llm(
        self,
        *,
        task_id: str,
        retry_index: int,
        mode: Mode,
        alarm: Alarm,
        language: str,
        previous_feedback: PreviousAuditFeedback | None,
        previous_audit: AuditDoneFailedEvent | None,
        allowed_models: list[str],
        default_models: list[str],
        default_gw_input: GWInput,
    ) -> dict[str, Any]:
        fallback = {
            "gw_input": default_gw_input.to_dict(),
            "cp_strategy": {
                "models": list(default_models),
                "task_append": "",
            },
            "rationale": "llm_decision_disabled",
        }
        if not self.config.enable_llm_decision:
            return fallback
        if not self.config.llm_model:
            fallback["rationale"] = "llm_model_not_configured"
            return fallback

        base_request_params = {
            "model_class": self._normalize_decision_model_class(self.config.llm_model_class),
            "model_name": self.config.llm_model,
            "model_kwargs": {
                "temperature": self.config.llm_temperature,
                "max_tokens": self.config.llm_max_tokens,
                "max_completion_tokens": self.config.llm_max_tokens,
                "timeout": self.config.llm_timeout,
            },
        }

        # Extract linter violations from previous audit for enhanced L2 analysis
        linter_violations_summary = None
        if previous_feedback and previous_feedback.failed_level == "L2":
            linter_violations_summary = self._format_linter_violations_for_llm(previous_feedback)

        user_payload = {
            "task_id": task_id,
            "retry_index": retry_index,
            "forced_mode": mode,
            "language": language,
            "alarm": alarm.to_dict(),
            "previous_audit_feedback": previous_feedback.to_dict() if previous_feedback else None,
            "previous_audit_raw": previous_audit.to_dict() if previous_audit else None,
            "linter_violations_summary": linter_violations_summary,
            "allowed_models": allowed_models,
            "default_models": default_models,
            "default_gw_input": default_gw_input.to_dict(),
            "model_selection_policy_hint": self._model_selection_policy_hint(
                retry_index=retry_index,
                previous_feedback=previous_feedback,
                allowed_models=allowed_models,
                default_models=default_models,
            ),
        }

        # Retry strategy to prevent truncation and invalid control signals.
        # 1) Baseline strict JSON schema.
        # 2) Larger token cap with compact prompt.
        # 3) Final compact fallback.
        plan = [
            {
                "attempt": 1,
                "max_completion_tokens": int(base_request_params["model_kwargs"]["max_completion_tokens"]),
                "compact": False,
            },
            {
                "attempt": 2,
                "max_completion_tokens": int(base_request_params["model_kwargs"]["max_completion_tokens"]) * 2,
                "compact": True,
            },
            {
                "attempt": 3,
                "max_completion_tokens": int(base_request_params["model_kwargs"]["max_completion_tokens"]) * 3,
                "compact": True,
            },
        ]

        last_error: dict[str, Any] | None = None
        for step in plan:
            request_params = json.loads(json.dumps(base_request_params))
            request_params["model_kwargs"]["max_completion_tokens"] = max(128, int(step["max_completion_tokens"]))
            request_params["model_kwargs"]["max_tokens"] = max(128, int(step["max_completion_tokens"]))

            messages = self._decision_messages(user_payload=user_payload, compact=bool(step["compact"]))
            start = perf_counter()
            trace: dict[str, Any] = {
                "trace_backend": "ao-llm-decision",
                "agent": "AO",
                "worker_id": "ao-decision",
                "provider": self.config.llm_model_class,
                "model_id": self.config.llm_model,
                "request_params": request_params,
                "messages": messages,
                "started_at": utc_now_iso(),
                "retry_attempt": int(step["attempt"]),
            }

            try:
                response = self._query_decision_llm(messages=messages, request_params=request_params)
                trace["response"] = to_jsonable(response)
                finish_reason = self._extract_finish_reason(response)
                trace["finish_reason"] = finish_reason
                decision_raw, parse_status, parse_error = self._parse_llm_decision(response)
                trace["parse_status"] = parse_status
                if parse_error:
                    trace["parse_error"] = parse_error

                if finish_reason == "length":
                    trace["duration_ms"] = round((perf_counter() - start) * 1000, 3)
                    self.last_llm_traces.append(trace)
                    last_error = {
                        "type": "truncated_response",
                        "message": "AO decision response truncated by model length limit",
                        "finish_reason": finish_reason,
                    }
                    continue

                normalized, valid, validation_error = self._normalize_and_validate_decision(
                    decision_raw=decision_raw,
                    allowed_models=allowed_models,
                    default_models=default_models,
                    default_gw_input=default_gw_input,
                )
                trace["normalized_decision"] = to_jsonable(normalized)
                if validation_error:
                    trace["validation_error"] = validation_error

                trace["duration_ms"] = round((perf_counter() - start) * 1000, 3)
                self.last_llm_traces.append(trace)

                if not valid:
                    last_error = {
                        "type": "schema_invalid",
                        "message": validation_error or "invalid_decision_payload",
                        "finish_reason": finish_reason,
                        "parse_status": parse_status,
                    }
                    continue

                normalized.setdefault("rationale", "llm_decision")
                return normalized
            except Exception as exc:
                trace["error"] = {"type": type(exc).__name__, "message": str(exc)}
                trace["duration_ms"] = round((perf_counter() - start) * 1000, 3)
                self.last_llm_traces.append(trace)
                last_error = trace["error"]

        fallback["rationale"] = "llm_decision_invalid_fallback"
        if last_error is not None:
            fallback["error"] = last_error
        return fallback

    @staticmethod
    def _decision_messages(*, user_payload: dict[str, Any], compact: bool) -> list[dict[str, str]]:
        if compact:
            system_prompt = (
                "Output JSON only. Schema: "
                "{gw_input:{build_semantic_graph:boolean,extract_constraints:boolean},"
                "cp_strategy:{models:string[],model_count:integer,task_append:string},"
                "rationale:string}. "
                "models MUST be chosen from allowed_models. No markdown, no prose."
            )
        else:
            system_prompt = (
            "You are AO (Adaptive Orchestrator), the strategic decision engine in OH-MAS for OpenHarmony code defect repair.\\n\\n"
            "# Your Mission\\n"
            "Analyze alarm details and prior audit feedback to determine optimal execution strategy for downstream agents "
            "(GW: Graph Weaver, CP: Constrained Patcher).\\n\\n"
            "# Decision Process (follow in order)\\n"
            "1. **Assess alarm severity and retry context**\\n"
            "   - First attempt (retry_index=0): Favor single-model, minimal-graph exploration\\n"
            "   - Failed attempts (retry_index>0): Escalate model diversity and graph depth based on failed_level\\n\\n"
            "2. **Interpret previous audit feedback**\\n"
            "   - L1 (format): Focus on patch applicability and diff stability\\n"
            "   - L2 (target alarm): Focus on precise target alarm elimination\\n"
            "     * CRITICAL: Check if user_payload includes linter_violations_summary\\n"
            "     * If present: Use exact violation lines/snippets to guide task_append\\n"
            "     * These are the EXACT locations linter detected - not guesses\\n"
            "   - L3 (regression): Focus on minimal change scope and regression avoidance\\n"
            "   - No feedback: Use mode default strategy\\n\\n"
            "3. **Select GW strategy**\\n"
            "   - build_semantic_graph: Enable if retry_index>=1 OR previous_feedback.failed_level in [L2,L3]\\n"
            "   - extract_constraints: Enable if retry_index>=1 OR previous_feedback.failed_level==L3\\n"
            "   - **SPECIAL RULE**: For alarm.rule == '@performance/hp-arkui-use-reusable-component',\\n"
            "     ALWAYS set BOTH build_semantic_graph=true AND extract_constraints=true regardless of\\n"
            "     retry_index. This rule has complex multi-file fix patterns (multiple LazyForEach\\n"
            "     blocks, nested custom component inlining, Visibility-based if-else elimination) that\\n"
            "     require a full repair contract even on the very first attempt (easy mode).\\n\\n"
            "4. **Select CP models**\\n"
            "   Model count is determined by execution mode (not retry index):\\n"
            "   - easy: 1 model (low-cost probing)\\n"
            "   - medium: 2 models (introduce complementarity)\\n"
            "   - hard: 3 models (maximize coverage)\\n"
            "   Use default_models as the baseline, then adjust based on:\\n"
            "   - alarm category and language layer (ArkTS vs C++)\\n"
            "   - previous failure type (L1/L2/L3) and diagnostics\\n"
            "   - Avoid repeating exact model sets from previous failed attempts if diagnostics indicate the model was a poor fit\\n"
            "   - CRITICAL: Only select from allowed_models array\\n\\n"
            "5. **Formulate task_append guidance (MOST CRITICAL for retries)**\\n"
            "   The task_append field is the PRIMARY mechanism to prevent CP from repeating failures.\\n"
            "   CP models may dismiss vague guidance as 'false positives' - you MUST be SPECIFIC and AUTHORITATIVE.\\n\\n"
            "   ## For L1 (Patch Not Applicable) failures:\\n"
            "   task_append MUST include:\\n"
            "   - Exact files and line numbers where patch failed (from patch_diagnostics[].details)\\n"
            "   - Explicit instruction: 'MANDATORY: Re-read ALL target files using read_file BEFORE editing. "
            "DO NOT reuse old_str from memory or previous attempts.'\\n"
            "   - If details mention specific line mismatches, quote them\\n\\n"
            "   Example L1 task_append:\\n"
            "   '=== MANDATORY CONSTRAINTS ===\\n"
            "   Previous patches failed to apply at CardComponent.ets:16 and CardLongTakePageOne.ets:20.\\n"
            "   REQUIRED ACTIONS:\\n"
            "   1. Use read_file to get CURRENT content of each file BEFORE editing\\n"
            "   2. DO NOT copy old_str from previous attempts - file content may have changed\\n"
            "   3. Ensure old_str matches EXACTLY including whitespace\\n"
            "   4. If CardClickRegistry already exists, do NOT add it again'\\n\\n"
            "   ## For L2 (Target Alarm Not Fixed) failures:\\n"
            "   CRITICAL: You receive linter_violations[] with code_snippet showing EXACT problematic code.\\n"
            "   Your task_append MUST:\\n"
            "   1. Display the code_snippet from linter_violations\\n"
            "   2. Analyze what in the code_snippet triggers the rule (root cause analysis)\\n"
            "   3. Explain WHY previous patches failed (misidentified the problem, wrong location, etc.)\\n"
            "   4. Point to the SPECIFIC lines/patterns that need attention\\n"
            "   Example L2 task_append:\\n"
            "   '=== MANDATORY CONSTRAINTS ===\\n"
            "   **Failure Type:** L2 - Target Alarm Not Fixed\\n\\n"
            "   **Violations Found:**\\n"
            "   Linter still detects `hp-arkui-no-func-as-arg-for-reusable-component` at:\\n"
            "   - ContactDetailComponent.ets:51\\n"
            "     ```typescript\\n"
            "     ContactDetailComponent({ rawContactId: this.getRawContactId(index) })\\n"
            "     ```\\n\\n"
            "   **Root Cause Analysis:**\\n"
            "   The code shows this.getRawContactId(index) being CALLED as a component parameter.\\n"
            "   This is a METHOD CALL violation (Type 2), not a function member definition.\\n"
            "   Previous patches removed onClickHead which was NOT the actual violation.\\n\\n"
            "   **What CP Should Focus On:**\\n"
            "   The method call at line 51 needs to be pre-computed before being passed as a parameter.\\n"
            "   Refer to knowledge_pack for specific @Reusable parameter handling patterns.'\\n\\n"
            "   ## For L3 (New Warnings Introduced) failures:\\n"
            "   CRITICAL: You receive introduced_warnings[] with code_snippet showing problematic code.\\n"
            "   Your task_append MUST:\\n"
            "   1. Display the code_snippet from introduced_warnings\\n"
            "   2. Analyze what in the code_snippet triggers the NEW rule\\n"
            "   3. Explain WHY your previous patch caused this (e.g., added @Reusable but kept custom component calls)\\n"
            "   4. State that these are NOT false positives - linter is correct\\n"
            "   Example L3 task_append:\\n"
            "   '=== MANDATORY CONSTRAINTS ===\\n"
            "   **Failure Type:** L3 - Introduced New Warnings\\n\\n"
            "   **Violations Found:**\\n"
            "   Previous patch introduced `@performance/hp-arkui-replace-nested-reusable-component-by-builder` at:\\n"
            "   - VideoSwipeComponent.ets:42\\n"
            "     ```typescript\\n"
            "     VideoSwipePlayer({ url: this.url })  // Inside @Reusable component build()\\n"
            "     ```\\n\\n"
            "   **Root Cause Analysis:**\\n"
            "   The previous patch added @Reusable decorator to VideoSwipeComponent, but its build() method\\n"
            "   still calls custom component VideoSwipePlayer(). ArkUI prohibits custom component calls\\n"
            "   inside @Reusable components because they break the recycling mechanism.\\n\\n"
            "   **What CP Should Focus On:**\\n"
            "   The custom component call at line 42 violates @Reusable constraints.\\n"
            "   Refer to knowledge_pack for @Reusable and nested component handling patterns.'\\n\\n"
            "   ## ANTI-PATTERNS (NEVER do these):\\n"
            "   - NEVER say 'avoid new warnings' without specifying WHICH rules\\n"
            "   - NEVER say 'be careful' or 'ensure compatibility' without specific actions\\n"
            "   - NEVER give generic advice like 'focus on patch applicability'\\n"
            "   - NEVER omit the MANDATORY CONSTRAINTS header for retry scenarios\\n\\n"
            "# Output Requirements\\n"
            "Return ONLY valid JSON with this exact schema:\\n"
            "{\\n"
            '  "gw_input": {\\n'
            '    "build_semantic_graph": boolean,\\n'
            '    "extract_constraints": boolean\\n'
            "  },\\n"
            '  "cp_strategy": {\\n'
            '    "models": [string],  // MUST be subset of allowed_models\\n'
            '    "model_count": integer,  // MUST match models array length\\n'
            '    "task_append": string    // Actionable guidance for CP workers - MUST follow format above for retries\\n'
            "  },\\n"
            '  "rationale": string  // Brief explanation of decision logic (max 50 words)\\n'
            "}\\n\\n"
            "# Quality Checklist (verify before responding)\\n"
            "- [ ] All models in cp_strategy.models exist in allowed_models\\n"
            "- [ ] model_count equals length of models array\\n"
            "- [ ] gw_input booleans align with retry_index and failed_level\\n"
            "- [ ] For retries: task_append starts with '=== MANDATORY CONSTRAINTS ===' header\\n"
            "- [ ] For retries: task_append references SPECIFIC rules/files/lines from patch_diagnostics\\n"
            "- [ ] JSON is valid and contains no markdown code fences"
            )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

    @staticmethod
    def _parse_llm_decision(response: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        content = AOAgent._extract_response_content(response)
        if not isinstance(content, str):
            return {}, "invalid_content", "response content is not a string"
        content = content.strip()
        if not content:
            return {}, "empty_content", "response content is empty"
        if content.startswith("```"):
            lines = content.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            if lines and lines[0].strip().lower() == "json":
                lines = lines[1:]
            content = "\n".join(lines).strip()
        try:
            raw = json.loads(content)
        except Exception as exc:
            return {}, "invalid_json", str(exc)
        if not isinstance(raw, dict):
            return {}, "invalid_shape", "top-level JSON is not an object"
        return raw, "ok", ""

    def _normalize_and_validate_decision(
        self,
        *,
        decision_raw: dict[str, Any],
        allowed_models: list[str],
        default_models: list[str],
        default_gw_input: GWInput,
    ) -> tuple[dict[str, Any], bool, str]:
        gw_input_raw = decision_raw.get("gw_input")
        cp_strategy_raw = decision_raw.get("cp_strategy")
        if not isinstance(gw_input_raw, dict):
            return {}, False, "gw_input must be an object"
        if not isinstance(cp_strategy_raw, dict):
            return {}, False, "cp_strategy must be an object"

        if not isinstance(gw_input_raw.get("build_semantic_graph"), bool):
            return {}, False, "gw_input.build_semantic_graph must be boolean"
        if not isinstance(gw_input_raw.get("extract_constraints"), bool):
            return {}, False, "gw_input.extract_constraints must be boolean"

        models_raw = cp_strategy_raw.get("models")
        if not isinstance(models_raw, list):
            return {}, False, "cp_strategy.models must be an array"
        requested_models = [str(m).strip() for m in models_raw if isinstance(m, str) and str(m).strip()]
        allowed_set = set(allowed_models)
        selected = self._dedupe([m for m in requested_models if m in allowed_set])

        model_count_raw = cp_strategy_raw.get("model_count")
        if model_count_raw is None:
            model_count = len(selected) if selected else 0
        elif isinstance(model_count_raw, int) and not isinstance(model_count_raw, bool):
            model_count = int(model_count_raw)
        else:
            return {}, False, "cp_strategy.model_count must be an integer if provided"

        if model_count < 0:
            return {}, False, "cp_strategy.model_count must be >= 0"

        if allowed_models:
            if model_count == 0 and selected:
                model_count = len(selected)
            if model_count > len(allowed_models):
                return {}, False, "cp_strategy.model_count exceeds allowed models size"
            if model_count > 0:
                ordered: list[str] = []
                for model in selected:
                    if model not in ordered:
                        ordered.append(model)
                for model in allowed_models:
                    if len(ordered) >= model_count:
                        break
                    if model not in ordered:
                        ordered.append(model)
                selected = ordered[:model_count]
            elif not selected:
                selected = list(default_models)

        if not selected and default_models:
            selected = list(default_models)

        if not selected and allowed_models:
            selected = [allowed_models[0]]

        normalized = {
            "gw_input": {
                "build_semantic_graph": bool(gw_input_raw["build_semantic_graph"]),
                "extract_constraints": bool(gw_input_raw["extract_constraints"]),
            },
            "cp_strategy": {
                "models": selected,
                "model_count": len(selected),
                "task_append": str(cp_strategy_raw.get("task_append") or ""),
            },
            "task_append": str(decision_raw.get("task_append") or ""),
            "rationale": str(decision_raw.get("rationale") or "llm_decision"),
        }
        return normalized, True, ""

    @staticmethod
    def _extract_response_content(response: dict[str, Any]) -> str:
        content = response.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(part for part in parts if part)
        if isinstance(response.get("text"), str):
            return response["text"]
        if isinstance(response.get("output_text"), str):
            return response["output_text"]
        return ""

    @staticmethod
    def _extract_finish_reason(response: dict[str, Any]) -> str:
        raw = ((response.get("extra") or {}).get("response") or {})
        choices = raw.get("choices") or []
        if not choices:
            return ""
        choice = choices[0] or {}
        finish_reason = choice.get("finish_reason") or choice.get("native_finish_reason") or ""
        return str(finish_reason)

    @staticmethod
    def _extract_ids(result: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for item in result.get("items", []):
            text_id = item.get("text_id")
            if isinstance(text_id, str) and text_id:
                out.append(text_id)
        return out

    @staticmethod
    def _normalize_decision_model_class(model_class: str) -> str:
        normalized = (model_class or "").strip().lower()
        if normalized in {"", "openrouter", "litellm"}:
            return normalized or "openrouter"
        return normalized

    def _query_decision_llm(self, *, messages: list[dict[str, str]], request_params: dict[str, Any]) -> dict[str, Any]:
        model_class = str(request_params.get("model_class") or "openrouter").lower()
        if model_class == "openrouter":
            return self._query_openrouter(messages=messages, request_params=request_params)
        raise RuntimeError(f"Unsupported AO decision model_class: {model_class}")

    def _query_openrouter(self, *, messages: list[dict[str, str]], request_params: dict[str, Any]) -> dict[str, Any]:
        import os

        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        model_name = str(request_params.get("model_name") or "").strip()
        model_kwargs = dict(request_params.get("model_kwargs") or {})
        resolved_model = model_name.split("/", 1)[1] if model_name.startswith("openrouter/") else model_name
        payload = {
            "model": resolved_model,
            "messages": messages,
            "temperature": model_kwargs.get("temperature", self.config.llm_temperature),
            "max_completion_tokens": int(model_kwargs.get("max_completion_tokens", self.config.llm_max_tokens)),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ao_decision",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "gw_input": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "build_semantic_graph": {"type": "boolean"},
                                    "extract_constraints": {"type": "boolean"},
                                },
                                "required": ["build_semantic_graph", "extract_constraints"],
                            },
                            "cp_strategy": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "models": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "model_count": {"type": "integer", "minimum": 0},
                                    "task_append": {"type": "string"},
                                },
                                "required": ["models", "model_count", "task_append"],
                            },
                            "rationale": {"type": "string"},
                        },
                        "required": ["gw_input", "cp_strategy", "rationale"],
                    },
                },
            },
        }
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        timeout = int(model_kwargs.get("timeout", self.config.llm_timeout))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"openrouter_http_{exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"openrouter_url_error: {exc}") from exc

        raw = json.loads(body)
        choice = ((raw.get("choices") or [{}])[0] or {}).get("message") or {}
        return {
            "role": str(choice.get("role") or "assistant"),
            "content": choice.get("content") or "",
            "extra": {
                "response": raw,
                "provider": "openrouter",
            },
        }

    def _build_kb_results(
        self,
        *,
        alarm: Alarm,
        framework: dict[str, Any],
        rule_templates: dict[str, Any],
        experiences: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        fw = self._ensure_non_empty_kb_result(
            framework,
            layer="L2",
            source="framework",
            alarm=alarm,
            allow_synthetic=True,
        )
        rt = self._ensure_non_empty_kb_result(
            rule_templates,
            layer="L2",
            source="rule_template",
            alarm=alarm,
            allow_synthetic=True,
        )
        ex = self._ensure_non_empty_kb_result(
            experiences,
            layer="L3",
            source="experience",
            alarm=alarm,
            allow_synthetic=False,
        )
        return {
            "framework": fw,
            "rule_templates": rt,
            "experiences": ex,
        }

    @staticmethod
    def _ensure_non_empty_kb_result(
        result: dict[str, Any],
        *,
        layer: str,
        source: str,
        alarm: Alarm,
        allow_synthetic: bool,
    ) -> dict[str, Any]:
        out = dict(result or {})
        items = list(out.get("items") or [])
        if items:
            out["items"] = items
            out["total"] = len(items)
            return out
        if not allow_synthetic:
            out["items"] = []
            out["total"] = 0
            out.setdefault("degraded", True)
            out.setdefault("degrade_reason", "empty_data")
            out.setdefault("latency_ms", 0)
            out.setdefault("kb_version", "unknown")
            out.setdefault("error", None)
            return out
        text_id = f"{layer}:{alarm.rule}:synthetic:0"
        synthetic = {
            "text_id": text_id,
            "layer": layer,
            "source": f"{source}_synthetic",
            "title": f"{layer} synthetic guidance",
            "text": (
                f"Rule={alarm.rule}; file={alarm.file}; message={alarm.message}. "
                "Generate an applicable unified diff and avoid introducing new warnings."
            ),
        }
        out["items"] = [synthetic]
        out["total"] = 1
        out["degraded"] = False
        out["degrade_reason"] = "synthetic_fallback"
        out.setdefault("latency_ms", 0)
        out.setdefault("kb_version", "synthetic_v1")
        out["error"] = None
        return out

    @staticmethod
    def _infer_language(path: str) -> str:
        lower = path.lower()
        if lower.endswith((".ets", ".ts", ".js")):
            return "arkts"
        if lower.endswith((".c", ".cc", ".cpp", ".cxx", ".h", ".hpp")):
            return "cpp"
        return "unknown"

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = item.strip()
            if normalized and normalized not in seen:
                out.append(normalized)
                seen.add(normalized)
        return out

    def _allowed_models_for_mode(self, mode: Mode) -> list[str]:
        configured_mode_models = list(self.config.mode_models.get(mode, []))
        registry = [item for item in self.config.models_registry if isinstance(item, str) and item.strip()]
        if not registry:
            return self._dedupe(configured_mode_models)
        return self._dedupe(registry)

    def _default_models_for_mode(self, mode: Mode, allowed_models: list[str]) -> list[str]:
        if not allowed_models:
            return []
        configured_mode_models = list(self.config.mode_models.get(mode, []))
        allowed_set = set(allowed_models)
        defaults = [model for model in configured_mode_models if model in allowed_set]
        defaults = self._dedupe(defaults)
        if defaults:
            return defaults
        return [allowed_models[0]]

    @staticmethod
    def _resolve_gw_input(decision: dict[str, Any], default_gw_input: GWInput) -> GWInput:
        raw = decision.get("gw_input")
        if not isinstance(raw, dict):
            return default_gw_input
        return GWInput(
            build_semantic_graph=bool(raw.get("build_semantic_graph", default_gw_input.build_semantic_graph)),
            extract_constraints=bool(raw.get("extract_constraints", default_gw_input.extract_constraints)),
        )

    def _resolve_cp_models(
        self,
        decision: dict[str, Any],
        allowed_models: list[str],
        default_models: list[str],
    ) -> list[str]:
        if not allowed_models:
            return list(default_models)
        cp_strategy = decision.get("cp_strategy")
        if not isinstance(cp_strategy, dict):
            return list(default_models)
        requested_models = cp_strategy.get("models")
        if not isinstance(requested_models, list):
            requested_models = []
        allowed_set = set(allowed_models)
        selected = self._dedupe(
            [
                str(model).strip()
                for model in requested_models
                if isinstance(model, str) and str(model).strip() in allowed_set
            ]
        )
        raw_model_count = cp_strategy.get("model_count")
        model_count = 0
        if isinstance(raw_model_count, int) and not isinstance(raw_model_count, bool) and raw_model_count > 0:
            model_count = max(1, min(raw_model_count, len(allowed_models)))
        if model_count > 0:
            ordered: list[str] = []
            for model in selected:
                if model not in ordered:
                    ordered.append(model)
            for model in allowed_models:
                if model not in ordered:
                    ordered.append(model)
                if len(ordered) >= model_count:
                    break
            return ordered[:model_count]
        if selected:
            return selected
        return list(default_models)

    @staticmethod
    def _format_linter_violations_for_llm(previous_feedback: PreviousAuditFeedback) -> dict | None:
        """Format linter violations from L2 failures for LLM consumption.

        Returns a structured summary that helps LLM understand EXACT violation locations.
        """
        all_violations = []
        for diag in previous_feedback.patch_diagnostics:
            if diag.failed_level == "L2" and diag.linter_violations:
                for violation in diag.linter_violations:
                    all_violations.append({
                        "patch_id": diag.patch_id,
                        "line": violation.line,
                        "column": violation.column,
                        "message": violation.message,
                        "code_snippet": violation.code_snippet,
                        "file": violation.file
                    })

        if not all_violations:
            return None

        return {
            "violation_count": len(all_violations),
            "violations": all_violations,
            "hint": "These are EXACT locations from linter output. Focus task_append on these specific lines."
        }

    @staticmethod
    def _model_selection_policy_hint(
        *,
        retry_index: int,
        previous_feedback: PreviousAuditFeedback | None,
        allowed_models: list[str],
        default_models: list[str],
    ) -> dict[str, Any]:
        preferred_count = 1 if retry_index == 0 else min(len(allowed_models), max(2, len(default_models)))
        failed_level = previous_feedback.failed_level if previous_feedback is not None else ""
        focus_by_level = {
            "L1": "patch_applicability_and_diff_stability",
            "L2": "target_alarm_elimination",
            "L3": "regression_avoidance_and_minimal_change",
        }
        return {
            "preferred_model_count": preferred_count,
            "default_models": list(default_models),
            "allowed_models_count": len(allowed_models),
            "failed_level_focus": focus_by_level.get(failed_level, "balanced"),
            "strategy": (
                "retry0_single_model_then_expand"
                if retry_index == 0
                else "expand_model_diversity_and_count"
            ),
        }
