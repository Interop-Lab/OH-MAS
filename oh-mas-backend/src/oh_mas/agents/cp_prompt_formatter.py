"""Structured prompt formatters for CP Worker.

This module provides clean, human-readable formatting for CP Worker prompts,
replacing raw JSON dumps with structured markdown sections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_mas.core.schemas import (
        Alarm,
        ContextReadyEvent,
        PreviousAuditFeedback,
        TaskProfiledEvent,
    )


def format_alarm_section(alarm: Alarm) -> str:
    """Format alarm information in a clear, structured way.

    Args:
        alarm: The Alarm object containing violation details

    Returns:
        Formatted alarm section as markdown
    """
    return f"""# Target Alarm

**File:** `{alarm.file}`
**Rule:** `{alarm.rule}`
**Location:** Lines {alarm.line_start}-{alarm.line_end}
**Message:** {alarm.message}
"""


def format_context_section(context: ContextReadyEvent) -> str:
    """Format GW-provided context in structured form.

    Args:
        context: ContextReadyEvent containing graph slice, constraints, and snippets

    Returns:
        Formatted context section
    """
    sections = ["# Context from GW Agent\n"]

    # Mode indicator
    context_mode = "precise" if context.precise_slice else "graph-based"
    sections.append(f"**Context Mode:** {context_mode}\n")

    # Constraints (merged from multiple sources)
    all_constraints = list(context.constraints)
    if context.precise_slice:
        all_constraints.extend(context.precise_slice.get("constraints", []))

    if all_constraints:
        sections.append("## Constraints")
        for i, constraint in enumerate(all_constraints, 1):
            sections.append(f"{i}. {constraint}")
        sections.append("")

    # Relevant files (from precise slice)
    if context.precise_slice:
        relevant_files = context.precise_slice.get("relevant_files", [])
        if relevant_files:
            sections.append("## Relevant Files")
            for file_info in relevant_files:
                path = file_info.get("path", "")
                relevance = file_info.get("relevance", "")
                sections.append(f"- `{path}`: {relevance}")
            sections.append("")

        # Code snippets (location hints only, content excluded to save tokens)
        snippets = context.precise_slice.get("snippets", [])
        if snippets:
            sections.append("## Code Locations to Inspect")
            sections.append("Use `read_file` to examine these locations:\n")
            for snippet in snippets:
                file_path = snippet.get("file_path", "")
                name = snippet.get("name", "")
                start = snippet.get("start_line", 0)
                end = snippet.get("end_line", 0)
                relevance = snippet.get("relevance", "")
                sections.append(f"- **{name}** in `{file_path}` (lines {start}-{end})")
                if relevance:
                    sections.append(f"  *Relevance:* {relevance}")
            sections.append("")

        # GW reasoning (if available)
        reasoning = context.precise_slice.get("llm_reasoning", "")
        if reasoning:
            sections.append("## GW Analysis")
            sections.append(reasoning)
            sections.append("")

    return "\n".join(sections)


def format_knowledge_pack(cp_input_dict: dict) -> str:
    """Format AO Agent prompt injection for CP Worker.

    knowledge_pack (rule_templates + experiences) is now consumed by GW to synthesize
    the repair_contract triplet (must_fix / must_not_touch / allowed_transformations).
    CP receives the unified contract via ContextReadyEvent, NOT raw knowledge_pack.

    This function only renders prompt_injection.task_append, which AO uses to inject
    MANDATORY CONSTRAINTS in retry scenarios.

    Args:
        cp_input_dict: Dictionary from CPInput.to_worker_dict()

    Returns:
        Formatted prompt injection section, or empty string if nothing to inject
    """
    prompt_injection = cp_input_dict.get("prompt_injection", {})
    task_append = prompt_injection.get("task_append", "")
    if not task_append:
        return ""

    sections = [
        "## Additional Instructions from AO",
        task_append,
        "",
    ]
    return "\n".join(sections)


def format_retry_protocol(
    profiled: TaskProfiledEvent,
    previous_feedback: PreviousAuditFeedback,
) -> str:
    """Format retry execution protocol (references AO's analysis, no data duplication).

    NOTE: Failure diagnostics, violations, and code snippets are provided by AO Agent
    via task_append (MANDATORY CONSTRAINTS section). This function only provides
    execution workflow that references AO's analysis.

    Args:
        profiled: TaskProfiledEvent containing retry context
        previous_feedback: PreviousAuditFeedback from DA Agent

    Returns:
        Formatted retry execution protocol
    """
    sections = [
        "=" * 60,
        "===  RETRY EXECUTION PROTOCOL  ===",
        "=" * 60,
        "",
        f"**Retry Attempt:** #{profiled.retry_index}",
        f"**Previous Failure:** {previous_feedback.failed_level} - {previous_feedback.reason}",
        "",
    ]

    # Provide failure-level specific execution guidance
    if previous_feedback.failed_level == "L1":
        sections.extend([
            "## L1 Failure Recovery Workflow",
            "",
            "Your previous patch failed to apply. Follow this workflow:",
            "",
            "1. **Review AO's MANDATORY CONSTRAINTS above** for exact failure locations",
            "2. **Re-read ALL target files** using `read_file <path>`",
            "   - Files may have changed since your last attempt",
            "   - DO NOT reuse old_str from memory",
            "3. **Copy exact strings** from current file content for old_str",
            "4. **Verify uniqueness** of old_str before calling edit_file",
            "",
        ])

    elif previous_feedback.failed_level == "L2":
        sections.extend([
            "## L2 Failure Recovery Workflow",
            "",
            "The target alarm still exists. AO has provided root cause analysis above.",
            "",
            "1. **Study AO's MANDATORY CONSTRAINTS section above** carefully",
            "   - Note EXACT line numbers where linter detected violations",
            "   - Review code snippets showing problematic patterns",
            "   - Understand AO's analysis of WHY previous patches failed",
            "2. **Read target file** with `read_file` to see current state",
            "3. **Plan your fix** targeting the SPECIFIC patterns analyzed by AO",
            "4. **Execute edit_file** for those exact locations",
            "",
        ])

    elif previous_feedback.failed_level == "L3":
        sections.extend([
            "## L3 Failure Recovery Workflow",
            "",
            "Your previous patch introduced new warnings. AO has analyzed them above.",
            "",
            "1. **Read AO's MANDATORY CONSTRAINTS section above**",
            "   - Note ALL introduced warning rules and locations",
            "   - Review AO's root cause analysis for each violation",
            "   - Understand WHY your previous patch triggered these warnings",
            "2. **Read EVERY file mentioned** in AO's analysis",
            "   - Use exact paths from the violations list",
            "   - If path fails, use `search_code <basename>` to locate it",
            "3. **Consult the repair contract** for transformation guidance on the introduced rules",
            "   - GW has synthesized allowed_transformations from repair experiences",
            "   - Study the fix patterns in the Allowed Transformations section",
            "4. **Address EACH introduced warning** based on:",
            "   - AO's root cause analysis (WHY it was triggered)",
            "   - Knowledge pack patterns (HOW to fix it)",
            "5. **Verify holistically** before submit_patch:",
            "   - Original alarm fixed",
            "   - No warnings from AO's list remain",
            "   - No new syntax errors introduced",
            "",
        ])

    # Universal execution rules
    sections.extend([
        "## Universal Execution Rules",
        "",
        "- Always `read_file` before `edit_file` (never trust memory)",
        "- Copy old_str from CURRENT file content (not previous attempts)",
        "- Ensure old_str is unique in the file",
        "- State your repair plan in one sentence before editing",
        "",
        "=" * 60,
        "",
    ])

    return "\n".join(sections)


def format_workflow_hint(step_limit: int, alarm_file: str) -> str:
    """Format recommended workflow section.

    Args:
        step_limit: Maximum steps allowed
        alarm_file: The file containing the alarm

    Returns:
        Formatted workflow hint
    """
    return f"""# Recommended Workflow

```bash
# Step 1: Inspect the alarm location
read_file {alarm_file}

# Step 2-{step_limit - 3}: Read other relevant files from context (optional)
# read_file <other_file>
# search_code <pattern>  # If you need to find related code

# Step {step_limit - 2}: Make your edits (can call multiple times)
edit_file {{"path": "...", "old_str": "...", "new_str": "..."}}

# Step {step_limit - 1}: Additional edits if needed (multi-file patches)
# edit_file {{"path": "...", "old_str": "...", "new_str": "..."}}

# Step {step_limit}: Submit the patch
submit_patch
```

**Strategy tips:**
- Reserve at least 2 steps for editing + submission
- Read files before editing to get current content
- Use snippets from context as hints, but always verify with read_file
- Multiple edit_file calls are allowed for complex repairs
"""
