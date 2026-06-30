from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from oh_mas.core.schemas import (
    Alarm,
    AuditDoneEvent,
    AuditDoneFailedEvent,
    AuditDonePassedEvent,
    IntroducedWarning,
    LinterViolation,
    PatchDiagnostic,
    PatchItem,
    PatchesReadyEvent,
)


@dataclass
class DAAuditConfig:
    repo_path: Path
    codelinter_target_cmd: str
    cppcheck_target_cmd: str
    codelinter_repo_cmd: str
    cppcheck_repo_cmd: str
    execution_mode: str = "host"
    docker_image: str = "harmonyrepair:latest"
    docker_workdir: str = "/workspace"
    docker_extra_args: str = ""
    git_bin: str = "git"
    codelinter_bin: str = "codelinter"
    cppcheck_bin: str = "cppcheck"
    require_tools_preflight: bool = True


@dataclass
class LintSnapshot:
    codelinter_items: list[dict]
    cppcheck_items: list[dict]
    codelinter_error: str = ""
    cppcheck_error: str = ""
    codelinter_raw_output: str = ""
    cppcheck_raw_output: str = ""
    # Evaluate-compatible warning tuples: (relpath_file, str_line, rule)
    warnings_set: set[tuple[str, str, str]] = field(default_factory=set)

    def tool_errors(self) -> list[str]:
        errors: list[str] = []
        if self.codelinter_error:
            errors.append(f"codelinter: {self.codelinter_error}")
        if self.cppcheck_error:
            errors.append(f"cppcheck: {self.cppcheck_error}")
        return errors


class NoRepoDAAgent:
    def audit(self, event: PatchesReadyEvent, alarm: Alarm) -> AuditDoneEvent:
        failed: list[str] = []
        diagnostics: list[PatchDiagnostic] = []
        for patch in event.patches:
            if self._looks_like_diff(patch.diff):
                return AuditDonePassedEvent(task_id=event.task_id, patch_id=patch.patch_id)
            failed.append(patch.patch_id)
            diagnostics.append(
                PatchDiagnostic(
                    patch_id=patch.patch_id,
                    failed_level="L1",
                    reason="No candidate produced valid unified diff",
                    tool="audit",
                    details="patch text did not match unified diff shape",
                )
            )
        return AuditDoneFailedEvent(
            task_id=event.task_id,
            failed_level="L1",
            reason="No candidate produced valid unified diff",
            failed_patches=failed,
            patch_diagnostics=diagnostics,
        )

    @staticmethod
    def _looks_like_diff(text: str) -> bool:
        return text.startswith("--- ") and "\n+++ " in text and "\n@@" in text


class DAAgent:
    def __init__(self, config: DAAuditConfig):
        self.config = config
        self._preflight_checked = False
        self._initial_commit: str | None = None

    def audit(self, event: PatchesReadyEvent, alarm: Alarm) -> AuditDoneEvent:
        self._ensure_preflight()

        # Always re-capture the HEAD commit at the start of each audit() call so
        # that retries or reused DAAgent instances reset to the correct baseline
        # rather than a stale commit from a prior invocation.
        try:
            result = self._run_cmd(f"{self.config.git_bin} rev-parse HEAD", check=True)
            self._initial_commit = result.stdout.strip()
        except Exception:
            self._initial_commit = None

        # Determine which linters to use based on instance_id prefix
        # OH_* -> ArkTS (codelinter only)
        # CPP_* -> C/C++ (cppcheck only)
        use_codelinter = event.task_id.startswith("OH_")
        use_cppcheck = event.task_id.startswith("CPP_")

        # ONE repo-level baseline snapshot — used for L2 and L3.
        # Mirrors evaluate_in_docker.py which runs one linter invocation before
        # patching and reuses the result for both target-alarm and regression checks.
        base_repo = self._collect_repo_snapshot(use_codelinter, use_cppcheck)
        failed: list[str] = []
        diagnostics: list[PatchDiagnostic] = []
        last_reason = "No candidate patch passed L1/L2/L3"
        last_level = "L1"

        for patch in event.patches:
            try:
                ok, reason, details = self._run_l1_with_repair(patch)
                if not ok:
                    failed.append(patch.patch_id)
                    last_reason = reason
                    last_level = "L1"
                    diagnostics.append(
                        PatchDiagnostic(
                            patch_id=patch.patch_id,
                            failed_level="L1",
                            reason=reason,
                            tool="git_apply",
                            details=details,
                        )
                    )
                    continue

                # ONE repo-level post-patch snapshot — used for syntax, L2, AND L3.
                # Mirrors evaluate_in_docker.py: single run_linter() after apply.
                post_repo = self._collect_repo_snapshot(use_codelinter, use_cppcheck)

                # Syntax check (between L1 and L2): detect compile/parse errors
                syntax_fail, syntax_reason = self._check_syntax_errors(post_repo)
                if syntax_fail:
                    failed.append(patch.patch_id)
                    last_reason = syntax_reason
                    last_level = "L2"
                    diagnostics.append(
                        PatchDiagnostic(
                            patch_id=patch.patch_id,
                            failed_level="L2",
                            reason=syntax_reason,
                            tool="codelinter/cppcheck",
                            details="syntax error detected in post-patch linter output",
                        )
                    )
                    continue

                l2_ok, l2_reason, l2_details, l2_violations = self._run_l2(base_repo, post_repo, alarm)
                if not l2_ok:
                    failed.append(patch.patch_id)
                    last_reason = l2_reason
                    last_level = "L2"
                    diagnostics.append(
                        PatchDiagnostic(
                            patch_id=patch.patch_id,
                            failed_level="L2",
                            reason=l2_reason,
                            tool="codelinter/cppcheck",
                            details=l2_details,
                            linter_violations=l2_violations,
                        )
                    )
                    continue

                # L3 uses the same post_repo snapshot (no separate scan).
                # Extract files modified by this patch for mixed-precision comparison.
                patched_files = self._extract_patched_files(patch.diff)
                l3_ok, l3_reason, l3_details, introduced_warnings = self._run_l3(
                    base_repo, post_repo, patched_files=patched_files
                )
                if l3_ok:
                    return AuditDonePassedEvent(task_id=event.task_id, patch_id=patch.patch_id)
                failed.append(patch.patch_id)
                last_reason = l3_reason
                last_level = "L3"
                diagnostics.append(
                    PatchDiagnostic(
                        patch_id=patch.patch_id,
                        failed_level="L3",
                        reason=l3_reason,
                        tool="codelinter/cppcheck",
                        details=l3_details,
                        introduced_warnings=introduced_warnings,
                    )
                )
            finally:
                self._cleanup_patch_file()

        return AuditDoneFailedEvent(
            task_id=event.task_id,
            failed_level=last_level,
            reason=last_reason,
            failed_patches=failed,
            patch_diagnostics=diagnostics,
        )

    def _ensure_preflight(self) -> None:
        if self._preflight_checked or not self.config.require_tools_preflight:
            return
        if not self.config.repo_path.exists():
            raise FileNotFoundError(f"DA repo_path does not exist: {self.config.repo_path}")
        if self.config.execution_mode == "host":
            for binary in (self.config.git_bin, self.config.codelinter_bin, self.config.cppcheck_bin):
                if binary and shutil.which(binary) is None:
                    raise RuntimeError(f"Required binary not found in PATH: {binary}")
        else:
            if not self.config.docker_image:
                raise RuntimeError("docker_image is required when execution_mode=docker")
        self._preflight_checked = True

    def _cleanup_patch_file(self) -> None:
        patch_file = self.config.repo_path / ".oh_mas_patch.diff"
        # Try to reverse the patch first
        self._run_cmd(f"{self.config.git_bin} apply -R --whitespace=nowarn .oh_mas_patch.diff", check=False)
        # Always do a hard reset to initial commit to ensure clean state for next patch
        if self._initial_commit:
            self._run_cmd(f"{self.config.git_bin} reset --hard {self._initial_commit}", check=False)
            self._run_cmd(f"{self.config.git_bin} clean -fd", check=False)
        if patch_file.exists():
            patch_file.unlink()

    def _run_l1_with_repair(self, patch: PatchItem) -> tuple[bool, str, str]:
        patch_file = self.config.repo_path / ".oh_mas_patch.diff"
        patch_file.write_text(patch.diff)

        check_cmd = f"{self.config.git_bin} apply --check --whitespace=nowarn .oh_mas_patch.diff"
        apply_cmd = f"{self.config.git_bin} apply --whitespace=nowarn .oh_mas_patch.diff"
        check = self._run_cmd(check_cmd, check=False)
        if check.returncode == 0:
           apply = self._run_cmd(apply_cmd, check=False)
           if apply.returncode == 0:
              return True, "", ""
           return False, f"Patch {patch.patch_id} apply failed", apply.stdout.strip()[:1000]

# EOF mismatch is common for model-generated diffs when the source file has no trailing newline.
        check_eof = self._run_cmd(f"{check_cmd} --inaccurate-eof", check=False)
        if check_eof.returncode == 0:
            apply_eof = self._run_cmd(f"{apply_cmd} --inaccurate-eof", check=False)
            if apply_eof.returncode == 0:
                return True, "", ""
            return False, f"Patch {patch.patch_id} apply failed with inaccurate-eof", apply_eof.stdout.strip()[:1000]

        repaired = self._repair_patch_text(patch.diff)
        if repaired == patch.diff:
            return False, f"Patch {patch.patch_id} not applicable", check.stdout.strip()[:1000]

        patch_file.write_text(repaired)
        repaired_check = self._run_cmd(check_cmd, check=False)
        if repaired_check.returncode == 0:
            repaired_apply = self._run_cmd(apply_cmd, check=False)
            if repaired_apply.returncode == 0:
                return True, "", ""
            return False, f"Patch {patch.patch_id} apply failed after repair", repaired_apply.stdout.strip()[:1000]

        repaired_check_eof = self._run_cmd(f"{check_cmd} --inaccurate-eof", check=False)
        if repaired_check_eof.returncode == 0:
            repaired_apply_eof = self._run_cmd(f"{apply_cmd} --inaccurate-eof", check=False)
            if repaired_apply_eof.returncode == 0:
                return True, "", ""
            return (
                False,
                f"Patch {patch.patch_id} apply failed after repair with inaccurate-eof",
                repaired_apply_eof.stdout.strip()[:1000],
            )
        return False, f"Patch {patch.patch_id} not applicable after repair", repaired_check.stdout.strip()[:1000]

    @staticmethod
    def _repair_patch_text(text: str) -> str:
        repaired = text.replace("\r\n", "\n")
        lines = repaired.split("\n")

        start_idx = 0
        for idx, line in enumerate(lines):
            if line.startswith("diff --git ") or line.startswith("--- "):
                start_idx = idx
                break
        trimmed = lines[start_idx:]

        allowed_prefixes = (
            "diff --git ",
            "index ",
            "--- ",
            "+++ ",
            "@@",
            "\\ No newline at end of file",
            " ",
            "+",
            "-",
        )
        valid: list[str] = []
        for line in trimmed:
            if line == "":
                # empty raw line is not valid in unified diff body; likely model-added trailer.
                break
            if line.startswith(allowed_prefixes):
                valid.append(line)
                continue
            break

        repaired = "\n".join(valid).replace("\\ No newline at end of file\n", "")

        # Normalize patch paths to git format (a/path and b/path)
        repaired = DAAgent._normalize_patch_paths(repaired)

        if not repaired.endswith("\n"):
            repaired += "\n"
        return repaired

    @staticmethod
    def _normalize_patch_paths(patch_text: str) -> str:
        """
        Normalize patch paths to standard git format.
        Fixes common issues:
        - Absolute paths like /tmp/file.ets -> b/file.ets
        - Paths starting with ./ like ./path/file.ets -> a/path/file.ets or b/path/file.ets
        - Missing a/ or b/ prefixes
        - Inconsistent paths between --- and +++ (uses --- as source of truth)
        """
        import os
        import re

        lines = patch_text.split("\n")
        normalized = []
        base_path = None  # Track the base path from --- line

        def extract_filename_from_path(path: str) -> str:
            """Extract the actual filename from a path, removing temp/workspace prefixes."""
            # Remove known temporary/workspace prefixes
            temp_prefixes = ["/tmp/", "/workspace/", "/var/tmp/", "/temp/"]
            for prefix in temp_prefixes:
                if path.startswith(prefix):
                    path = path[len(prefix):]
                    break

            # Remove leading ./
            if path.startswith("./"):
                path = path[2:]

            # If still has leading /, it's an absolute path - extract basename
            if path.startswith("/"):
                path_parts = path.split("/")
                # Take last 2 components if available (e.g., "module/file.ets"), else just filename
                if len(path_parts) >= 3:
                    path = "/".join(path_parts[-2:])
                elif len(path_parts) >= 2:
                    path = path_parts[-1]
                else:
                    path = path.lstrip("/")

            return path

        def get_filename_only(path: str) -> str:
            """Get just the filename (basename) from a path."""
            return os.path.basename(path.rstrip("/"))

        for line in lines:
            # Handle --- line (old file)
            if line.startswith("--- "):
                rest = line[4:].strip()
                # Extract path and optional timestamp
                parts = rest.split("\t", 1)
                path = parts[0].strip()
                timestamp = parts[1] if len(parts) > 1 else ""

                if path == "/dev/null":
                    normalized.append(line)
                    base_path = None
                    continue

                # Normalize the path
                path = extract_filename_from_path(path)

                # Add a/ prefix if not present and not /dev/null
                if not path.startswith("a/") and path != "/dev/null":
                    path = f"a/{path}"

                # Store the normalized path (without a/ prefix) for +++ line matching
                base_path = path[2:] if path.startswith("a/") else path

                # Reconstruct the line
                if timestamp:
                    normalized.append(f"--- {path}\t{timestamp}")
                else:
                    normalized.append(f"--- {path}")

            # Handle +++ line (new file)
            elif line.startswith("+++ "):
                rest = line[4:].strip()
                # Extract path and optional timestamp
                parts = rest.split("\t", 1)
                path = parts[0].strip()
                timestamp = parts[1] if len(parts) > 1 else ""

                if path == "/dev/null":
                    normalized.append(line)
                    continue

                # Normalize the path
                normalized_path = extract_filename_from_path(path)

                # If we have a base_path from ---, check if +++ path is just a basename
                # If so, use the base_path instead for consistency
                if base_path:
                    plus_basename = get_filename_only(normalized_path)
                    base_basename = get_filename_only(base_path)

                    # If +++ is just a filename and matches --- basename, use full --- path
                    if plus_basename == base_basename and "/" not in normalized_path:
                        normalized_path = base_path

                # Add b/ prefix if not present and not /dev/null
                if not normalized_path.startswith("b/") and normalized_path != "/dev/null":
                    normalized_path = f"b/{normalized_path}"

                # Reconstruct the line
                if timestamp:
                    normalized.append(f"+++ {normalized_path}\t{timestamp}")
                else:
                    normalized.append(f"+++ {normalized_path}")

            else:
                normalized.append(line)

        return "\n".join(normalized)


    _SYNTAX_MARKERS = ("SyntaxError", "Build failed", "Compile error", "Parse error")

    def _check_syntax_errors(self, snapshot: LintSnapshot) -> tuple[bool, str]:
        """Check if linter output contains syntax/compile error markers.

        Mirrors evaluate_in_docker.py's SYNTAX_FAIL check:
          new_warnings, new_output = run_linter(...)
          if any(m in new_output for m in syntax_markers): print("SYNTAX_FAIL")
        """
        for raw in (snapshot.codelinter_raw_output, snapshot.cppcheck_raw_output):
            if raw:
                for marker in self._SYNTAX_MARKERS:
                    if marker in raw:
                        return True, f"Syntax/compile error detected after patch: {marker}"
        return False, ""

    def _run_l2(self, base: LintSnapshot, post: LintSnapshot, alarm: Alarm) -> tuple[bool, str, str, list[LinterViolation]]:
        base_errors = base.tool_errors()
        post_errors = post.tool_errors()
        if base_errors or post_errors:
            details = {"base_errors": base_errors, "post_errors": post_errors}
            return False, "L2 checks unavailable due tool failure", json.dumps(details, ensure_ascii=False), []

        # Evaluate-compatible L2: check post-patch warnings_set for target alarm.
        # Mirrors evaluate_in_docker.py's check_target_warning() exactly.
        target_still_exists = self._check_target_warning(
            post.warnings_set, alarm.rule, alarm.file
        )
        if target_still_exists:
            violations = self._parse_linter_violations(post, alarm)
            matched_tuples = sorted(
                t for t in post.warnings_set
                if self._eval_file_match(t[0], alarm.file) and t[2] == alarm.rule
            )
            return False, "Target alarm still exists after patch", json.dumps(
                [list(t) for t in matched_tuples], ensure_ascii=False
            ), violations
        return True, "", "", []

    @staticmethod
    def _check_target_warning(
        warnings_set: set[tuple[str, str, str]],
        rule_id: str,
        target_file_rel: str,
    ) -> bool:
        """Mirror evaluate_in_docker.py's check_target_warning exactly.

        Uses Python ``in`` for bidirectional substring file matching and
        exact ``==`` for rule comparison.
        """
        for file_path, _line_no, w_rule in warnings_set:
            if target_file_rel in file_path or file_path in target_file_rel:
                if w_rule == rule_id:
                    return True
        return False

    @staticmethod
    def _eval_file_match(file_path: str, target_file_rel: str) -> bool:
        """Evaluate-compatible bidirectional substring file matching."""
        return target_file_rel in file_path or file_path in target_file_rel

    def _parse_linter_violations(self, snapshot: LintSnapshot, alarm: Alarm) -> list[LinterViolation]:
        """Parse linter output to extract specific violation locations for diagnostics.

        Uses evaluate-compatible file matching (substring ``in``) and exact rule
        matching on normalized values.
        """
        violations = []
        target_file_normalized = self._normalize_path(alarm.file)
        target_rule_normalized = self._normalize_rule(alarm.rule)

        # Parse codelinter items
        for item in snapshot.codelinter_items:
            item_file = self._extract_issue_file(item)
            item_rule = self._extract_issue_rule(item)
            item_line = self._extract_issue_line(item)

            if not self._eval_file_match(item_file, target_file_normalized):
                continue
            if item_rule != target_rule_normalized:
                continue

            message = self._extract_issue_message(item)
            column = item.get("column")
            repo_relative_file = self._extract_repo_relative_issue_file(item)
            code_snippet = ""
            if repo_relative_file and item_line:
                code_snippet = self._extract_code_snippet(
                    file_path=repo_relative_file,
                    line=item_line,
                    context_lines=2
                )

            violations.append(LinterViolation(
                line=item_line if item_line else 0,
                column=column,
                message=message,
                code_snippet=code_snippet,
                file=repo_relative_file
            ))

        # Parse cppcheck items (same matching logic)
        for item in snapshot.cppcheck_items:
            item_file = self._extract_issue_file(item)
            item_rule = self._extract_issue_rule(item)
            item_line = self._extract_issue_line(item)

            if not self._eval_file_match(item_file, target_file_normalized):
                continue
            if item_rule != target_rule_normalized:
                continue

            message = self._extract_issue_message(item)
            column = item.get("column")
            repo_relative_file = self._extract_repo_relative_issue_file(item)
            code_snippet = ""
            if repo_relative_file and item_line:
                code_snippet = self._extract_code_snippet(
                    file_path=repo_relative_file,
                    line=item_line,
                    context_lines=2
                )

            violations.append(LinterViolation(
                line=item_line if item_line else 0,
                column=column,
                message=message,
                code_snippet=code_snippet,
                file=repo_relative_file
            ))

        violations.sort(key=lambda v: v.line)
        return violations

    def _run_l3(
        self,
        base: LintSnapshot,
        post: LintSnapshot,
        patched_files: set[str] | None = None,
    ) -> tuple[bool, str, str, list[IntroducedWarning]]:
        """Run L3 regression check with mixed-precision comparison.

        Mirrors evaluate_in_docker.py's count_new_warnings() with patched_files support:

        - Files modified by the patch (patched_files): rule-type granularity.
          Line-number shifts caused by inserting new code are expected and NOT
          counted as regressions.  Only rules absent from the baseline for that
          file are flagged as truly new.
        - All other files: exact (file, line, rule) tuple comparison.
          Any new tuple in an unmodified file is a true regression.

        Args:
            base:          LintSnapshot before the patch.
            post:          LintSnapshot after the patch.
            patched_files: Set of repo-relative file paths modified by the patch.
                           If None or empty, falls back to original exact comparison.
        """
        base_errors = base.tool_errors()
        post_errors = post.tool_errors()
        if base_errors or post_errors:
            details = {"base_errors": base_errors, "post_errors": post_errors}
            return False, "L3 checks unavailable due tool failure", json.dumps(details, ensure_ascii=False), []

        raw_new = post.warnings_set - base.warnings_set

        if not raw_new:
            return True, "", "", []

        if not patched_files:
            # Fallback: original exact comparison
            new_warnings = raw_new
        else:
            # Pre-compute per-file rule sets from baseline (patched files only)
            base_rules_by_file: dict[str, set[str]] = {}
            for (fpath, _line, rule) in base.warnings_set:
                base_rules_by_file.setdefault(fpath, set()).add(rule)

            new_warnings: set[tuple[str, str, str]] = set()
            for (fpath, line, rule) in raw_new:
                is_patched = any(
                    fpath == pf or fpath.endswith(pf) or pf.endswith(fpath)
                    for pf in patched_files
                )
                if is_patched:
                    # Rule-level check: flag only rules absent from baseline for this file
                    if rule not in base_rules_by_file.get(fpath, set()):
                        new_warnings.add((fpath, line, rule))
                else:
                    # Strict exact-tuple for unmodified files
                    new_warnings.add((fpath, line, rule))

        if new_warnings:
            introduced_warnings = self._extract_introduced_warnings_from_tuples(post, new_warnings)
            sorted_tuples = sorted(new_warnings)
            return (
                False,
                f"New warnings introduced: {len(new_warnings)}",
                json.dumps([list(t) for t in sorted_tuples[:50]], ensure_ascii=False),
                introduced_warnings,
            )
        return True, "", "", []

    def _extract_introduced_warnings_from_tuples(
        self,
        snapshot: LintSnapshot,
        new_warning_tuples: set[tuple[str, str, str]],
    ) -> list[IntroducedWarning]:
        """Extract IntroducedWarning objects by matching evaluate-format tuples back to parsed items."""
        warnings: list[IntroducedWarning] = []
        repo_path_str = str(self.config.repo_path)

        for item in snapshot.codelinter_items:
            raw_file = item.get("file", "")
            line = item.get("line")
            rule = item.get("rule", "")
            if not raw_file or line is None or not rule:
                continue
            try:
                rel_file = os.path.relpath(raw_file, repo_path_str)
            except ValueError:
                rel_file = raw_file
            key = (rel_file, str(line), rule)
            if key not in new_warning_tuples:
                continue
            self._append_introduced_warning(warnings, item)
            if len(warnings) >= 10:
                return warnings

        for item in snapshot.cppcheck_items:
            raw_file = item.get("file", "")
            line = item.get("line")
            rule = item.get("rule", "")
            if not raw_file or not rule:
                continue
            try:
                rel_file = os.path.relpath(raw_file, repo_path_str)
            except ValueError:
                rel_file = raw_file
            line_str = str(line) if line is not None else "0"
            key = (rel_file, line_str, rule)
            if key not in new_warning_tuples:
                continue
            self._append_introduced_warning(warnings, item)
            if len(warnings) >= 10:
                return warnings

        return warnings

    def _append_introduced_warning(self, warnings: list[IntroducedWarning], item: dict) -> None:
        """Build an IntroducedWarning from an item dict and append it."""
        file_path = self._extract_issue_file(item)
        repo_relative_file = self._extract_repo_relative_issue_file(item)
        line = self._extract_issue_line(item)
        rule = self._extract_issue_rule(item)
        message = self._extract_issue_message(item)

        code_snippet = ""
        snippet_file = repo_relative_file or file_path
        if snippet_file and line:
            code_snippet = self._extract_code_snippet(snippet_file, line, context_lines=2)

        warnings.append(IntroducedWarning(
            file=file_path,
            line=line or 0,
            rule=rule,
            message=message,
            repo_relative_file=repo_relative_file,
            code_snippet=code_snippet,
        ))

    def _extract_repo_relative_issue_file(self, item: dict) -> str:
        """Return a CP-readable repo-relative path, preserving real filesystem casing."""
        raw_path = self._extract_issue_file_raw(item)
        if not raw_path:
            return ""

        candidate = raw_path.replace("\\", "/").strip()
        candidate = candidate.split(":", 1)[0] if re.match(r"^[A-Za-z]:", candidate) else candidate
        known_prefixes = (
            "/workspace/repo/",
            "/workspace/",
            str(self.config.repo_path).replace("\\", "/").rstrip("/") + "/",
        )
        for prefix in known_prefixes:
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):]
                break
        while candidate.startswith("./") or candidate.startswith("/"):
            candidate = candidate[2:] if candidate.startswith("./") else candidate[1:]

        direct = self.config.repo_path / candidate
        if direct.is_file():
            return direct.relative_to(self.config.repo_path).as_posix()

        return self._find_repo_relative_path_by_suffix(candidate)

    @staticmethod
    def _extract_issue_file_raw(item: dict) -> str:
        location = item.get("location")
        if isinstance(location, dict) and location.get("file"):
            return str(location.get("file")).replace("\\", "/").strip()
        for key in ("file", "filePath", "path", "filename", "fileName"):
            value = item.get(key)
            if value:
                return str(value).replace("\\", "/").strip()
        raw = item.get("raw")
        if raw:
            text = str(raw).replace("\\", "/").strip()
            match = re.match(r"^(.*?):\d+(?::\d+)?:", text)
            if match:
                return match.group(1).strip()
        return ""

    def _find_repo_relative_path_by_suffix(self, path: str) -> str:
        suffix = path.replace("\\", "/").strip().strip("/")
        if not suffix:
            return ""
        suffix_lower = suffix.lower()
        basename_lower = Path(suffix).name.lower()
        for candidate in self.config.repo_path.rglob("*"):
            if not candidate.is_file():
                continue
            if candidate.name.lower() != basename_lower:
                continue
            try:
                rel = candidate.relative_to(self.config.repo_path).as_posix()
            except ValueError:
                continue
            rel_lower = rel.lower()
            if rel_lower == suffix_lower or rel_lower.endswith(f"/{suffix_lower}"):
                return rel
        return suffix

    def _extract_code_snippet(self, file_path: str, line: int, context_lines: int = 2) -> str:
        """Extract code snippet around the given line with context.

        Returns formatted snippet like:
           39|   build() {
           40|     if (this.datasource?.type === 'video') {
        >> 41|       VideoSwipePlayer({    // <-- problem line
           42|         index: this.index,
           43|       });
        """
        try:
            # Try to find the file in repo_path
            full_path = self.config.repo_path / file_path
            if not full_path.exists():
                # Try with normalized path
                normalized = file_path.lstrip("/").lstrip("./")
                full_path = self.config.repo_path / normalized
                if not full_path.exists():
                    return ""

            lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(0, line - context_lines - 1)
            end = min(len(lines), line + context_lines)

            snippet_lines = []
            for i in range(start, end):
                line_num = i + 1
                line_content = lines[i] if i < len(lines) else ""
                prefix = ">>" if line_num == line else "  "
                snippet_lines.append(f"{prefix}{line_num:4d}| {line_content}")

            return "\n".join(snippet_lines)
        except Exception:
            return ""

    @staticmethod
    def _extract_patched_files(diff: str) -> set[str]:
        """Extract repo-relative file paths modified by a unified diff.

        Mirrors evaluate_in_docker.py's _extract_patched_files().
        Used to apply mixed-precision L3 comparison (line shifts in patched
        files are not counted as new violations).
        """
        patched: set[str] = set()
        if not diff:
            return patched
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                path = line[6:].split("\t", 1)[0].strip()
                if path and path != "/dev/null":
                    patched.add(path)
            elif line.startswith("+++ "):
                path = line[4:].split("\t", 1)[0].strip()
                if path and path not in ("/dev/null",) and not path.startswith("b/"):
                    patched.add(path)
        return patched

    # Mirror evaluate_in_docker.py: only lines with these severities enter warnings_set.
    _CODELINTER_ALLOWED_SEVERITIES: frozenset[str] = frozenset({"warn", "warning", "error", "suggestion"})

    def _build_warnings_set_codelinter(self, items: list[dict]) -> set[tuple[str, str, str]]:
        """Convert codelinter JSON items to evaluate-compatible (relpath, str_line, rule) tuples.

        Mirrors evaluate_in_docker.py's parse_codelinter_output which produces
        set((os.path.relpath(abs_path, project_dir), line_no_str, rule_id)).
        No lowercasing — raw values, matching evaluate.
        Severity filter: skip items whose severity is present but not in
        {warn, warning, error, suggestion}, matching evaluate's regex gate.
        """
        warnings: set[tuple[str, str, str]] = set()
        repo_path_str = str(self.config.repo_path)
        for item in items:
            raw_file = item.get("file", "")
            line = item.get("line")
            rule = item.get("rule", "")
            if not raw_file or line is None or not rule:
                continue
            # Mirror evaluate: regex only matches warn|error|suggestion severity lines.
            # If severity is populated but not in the allowed set, skip.
            severity = str(item.get("severity", "")).lower().strip()
            if severity and severity not in self._CODELINTER_ALLOWED_SEVERITIES:
                continue
            try:
                rel_file = os.path.relpath(raw_file, repo_path_str)
            except ValueError:
                rel_file = raw_file
            warnings.add((rel_file, str(line), rule))
        return warnings

    def _build_warnings_set_cppcheck(self, items: list[dict]) -> set[tuple[str, str, str]]:
        """Convert cppcheck items to evaluate-compatible (relpath, str_line, rule) tuples.

        Mirrors evaluate_in_docker.py's run_cppcheck which produces
        set((os.path.relpath(file, project_dir), line_no_str, f"cppcheck/{err_id}")).
        """
        warnings: set[tuple[str, str, str]] = set()
        repo_path_str = str(self.config.repo_path)
        for item in items:
            raw_file = item.get("file", "")
            line = item.get("line")
            rule = item.get("rule", "")
            if not raw_file or not rule:
                continue
            try:
                rel_file = os.path.relpath(raw_file, repo_path_str)
            except ValueError:
                rel_file = raw_file
            line_str = str(line) if line is not None else "0"
            warnings.add((rel_file, line_str, rule))
        return warnings

    def _collect_repo_snapshot(self, use_codelinter: bool = True, use_cppcheck: bool = True) -> LintSnapshot:
        codelinter_items, codelinter_error, codelinter_raw = [], "", ""
        cppcheck_items, cppcheck_error, cppcheck_raw = [], "", ""

        if use_codelinter:
            codelinter_items, codelinter_error, codelinter_raw = self._run_codelinter(self.config.codelinter_repo_cmd)

        if use_cppcheck:
            cppcheck_items, cppcheck_error, cppcheck_raw = self._run_cppcheck(self.config.cppcheck_repo_cmd)

        # Build evaluate-compatible warnings set from parsed items
        ws: set[tuple[str, str, str]] = set()
        if use_codelinter and not codelinter_error:
            ws |= self._build_warnings_set_codelinter(codelinter_items)
        if use_cppcheck and not cppcheck_error:
            ws |= self._build_warnings_set_cppcheck(cppcheck_items)

        return LintSnapshot(
            codelinter_items=codelinter_items,
            cppcheck_items=cppcheck_items,
            codelinter_error=codelinter_error,
            cppcheck_error=cppcheck_error,
            codelinter_raw_output=codelinter_raw,
            cppcheck_raw_output=cppcheck_raw,
            warnings_set=ws,
        )

    def _run_codelinter(self, cmd_template: str, *, target_file: str = "") -> tuple[list[dict], str, str]:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, dir=self.config.repo_path) as tmp:
            report = Path(tmp.name)
        cmd = cmd_template.format(
            report_file=str(report),
            target_file=target_file,
            repo_path=str(self.config.repo_path),
            codelinter_bin=self.config.codelinter_bin,
            cppcheck_bin=self.config.cppcheck_bin,
            git_bin=self.config.git_bin,
        )
        proc = self._run_cmd(cmd, check=False)

        items: list[dict] = []
        parse_error = ""
        if report.exists():
            try:
                raw_text = report.read_text()
                if raw_text.strip():
                    raw = json.loads(raw_text)
                    if isinstance(raw, list):
                        items = self._flatten_codelinter_output(raw)
                    elif isinstance(raw, dict):
                        for key in ("issues", "results", "data"):
                            if isinstance(raw.get(key), list):
                                items = self._flatten_codelinter_output(raw[key])
                                break
            except Exception as exc:
                items = []
                parse_error = f"report parse failed: {type(exc).__name__}: {exc}"
            finally:
                report.unlink(missing_ok=True)
        # If JSON report yielded nothing, fall back to evaluate-style text parsing.
        # Mirrors evaluate_in_docker.py which parses codelinter's stdout/stderr directly.
        if not items and not parse_error:
            items = self._parse_codelinter_text_output(proc.stdout)

        tool_error = ""
        if parse_error:
            tool_error = parse_error
        elif proc.returncode != 0 and not items:
            tool_error = f"exit={proc.returncode}, output={proc.stdout.strip()[:1000]}"
        return items, tool_error, proc.stdout

    @staticmethod
    def _flatten_codelinter_output(raw_items: list[dict]) -> list[dict]:
        """
        Flatten codelinter's nested output format to individual issue items.

        codelinter output format:
        [{"filePath": "...", "messages": [{"line": N, "rule": "...", "message": "..."}]}]

        Flattened format (what DA expects):
        [{"file": "...", "line": N, "rule": "...", "message": "..."}]
        """
        flat_items: list[dict] = []
        for entry in raw_items:
            if "filePath" in entry and "messages" in entry and isinstance(entry.get("messages"), list):
                file_path = entry.get("filePath", "")
                for msg in entry["messages"]:
                    flat_item = {
                        "file": file_path,
                        "line": msg.get("line"),
                        "column": msg.get("column"),
                        "rule": msg.get("rule", ""),
                        "message": msg.get("message", ""),
                        "severity": msg.get("severity", ""),
                    }
                    flat_items.append(flat_item)
            else:
                flat_items.append(entry)
        return flat_items

    @staticmethod
    def _parse_codelinter_text_output(output: str) -> list[dict]:
        """Fallback: parse codelinter plain text stdout (mirrors evaluate_in_docker.py parse_codelinter_output).

        Used when the JSON report file is absent or empty after running codelinter.
        Regex patterns and severity allowlist match evaluate_in_docker.py exactly.
        """
        items: list[dict] = []
        current_file: str | None = None

        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            # File header: "/abs/path/file.ets (N)"
            file_match = re.match(r'^(/[^\s]+\.(ets|ts|js))\s*\(\d+\)', line)
            if file_match:
                current_file = file_match.group(1)
                continue
            # Primary pattern: "12:3  warn  msg text  @rule-id"
            warn_match = re.match(
                r'^(\d+):(\d+)\s+(warn|error|suggestion)\s+(.+?)\s{2,}(@\S+(?:@[\w/-]+)?)$',
                line,
            )
            if warn_match and current_file:
                items.append({
                    "file": current_file,
                    "line": int(warn_match.group(1)),
                    "column": int(warn_match.group(2)),
                    "severity": warn_match.group(3),
                    "message": warn_match.group(4).strip(),
                    "rule": warn_match.group(5),
                })
                continue
            # Secondary pattern (single space before rule)
            warn_match2 = re.match(
                r'^(\d+):(\d+)\s+(warn|error)\s+(.+?)\s+(@\S+)$',
                line,
            )
            if warn_match2 and current_file:
                items.append({
                    "file": current_file,
                    "line": int(warn_match2.group(1)),
                    "column": int(warn_match2.group(2)),
                    "severity": warn_match2.group(3),
                    "message": warn_match2.group(4).strip(),
                    "rule": warn_match2.group(5),
                })
        return items

    def _run_cppcheck(self, cmd_template: str, *, target_file: str = "") -> tuple[list[dict], str, str]:
        cmd = cmd_template.format(
            target_file=target_file,
            repo_path=str(self.config.repo_path),
            codelinter_bin=self.config.codelinter_bin,
            cppcheck_bin=self.config.cppcheck_bin,
            git_bin=self.config.git_bin,
        )
        # Use split stderr: cppcheck writes XML to stderr.
        # Mirrors evaluate_in_docker.py which reads result.stderr for parsing.
        proc = self._run_cmd_split(cmd, check=False)
        items = self._parse_cppcheck_xml(proc.stderr)
        if not items:
            # Some cppcheck builds or Docker contexts route XML output
            # via stdout instead of stderr; try parsing stdout as fallback.
            items = self._parse_cppcheck_xml(proc.stdout)
        tool_error = ""
        if proc.returncode != 0 and not items:
            tool_error = f"exit={proc.returncode}, output={proc.stderr.strip()[:1000]}"
        return items, tool_error, proc.stderr

    @staticmethod
    def _parse_cppcheck_xml(output: str) -> list[dict]:
        """Parse cppcheck XML output (--xml --xml-version=2).

        Rule IDs are prefixed with 'cppcheck/' to match evaluate_in_docker.py format.
        Falls back to regex parsing if XML is malformed.
        """
        items: list[dict] = []
        skip_ids = {"checkersReport", "unmatchedSuppression"}

        try:
            root = ET.fromstring(output)
            for error in root.iter("error"):
                err_id = error.get("id", "")
                if err_id in skip_ids:
                    continue
                message = error.get("msg", error.get("verbose", "")).strip()
                rule = f"cppcheck/{err_id}" if err_id else ""
                for location in error.iter("location"):
                    file_path = location.get("file", "")
                    line_no = location.get("line", "0")
                    try:
                        line_int = int(line_no)
                    except (ValueError, TypeError):
                        line_int = 0
                    items.append({
                        "file": file_path,
                        "line": line_int,
                        "rule": rule,
                        "message": message,
                    })
        except ET.ParseError:
            for m in re.finditer(r'id="([^"]+)"[^>]*msg="([^"]*)"', output):
                err_id, err_msg = m.group(1), m.group(2)
                if err_id in skip_ids:
                    continue
                rule = f"cppcheck/{err_id}"
                locs = re.findall(r'file="([^"]+)"[^>]*line="(\d+)"', output)
                if locs:
                    for file_path, line_no in locs:
                        items.append({
                            "file": file_path,
                            "line": int(line_no),
                            "rule": rule,
                            "message": err_msg,
                        })
                else:
                    items.append({"rule": rule, "message": err_msg, "file": "", "line": 0})

        return items

    @staticmethod
    def _extract_issue_file(item: dict) -> str:
        location = item.get("location")
        if isinstance(location, dict) and location.get("file"):
            return DAAgent._normalize_path(location.get("file"))
        for key in ("file", "filePath", "path", "filename", "fileName"):
            value = item.get(key)
            if value:
                return DAAgent._normalize_path(value)
        raw = item.get("raw")
        if raw:
            return DAAgent._normalize_path(raw)
        return ""

    @staticmethod
    def _extract_issue_rule(item: dict) -> str:
        for key in ("rule", "ruleId", "id", "checker", "type"):
            value = item.get(key)
            if value:
                return DAAgent._normalize_rule(value)
        raw = item.get("raw")
        if raw:
            return DAAgent._normalize_rule(raw)
        return ""

    @staticmethod
    def _extract_issue_line(item: dict) -> int | None:
        for key in ("line", "lineNumber"):
            value = item.get(key)
            try:
                if value is not None and str(value).strip() != "":
                    return int(value)
            except Exception:
                pass
        location = item.get("location")
        if isinstance(location, dict):
            value = location.get("line")
            try:
                if value is not None and str(value).strip() != "":
                    return int(value)
            except Exception:
                pass
        raw = str(item.get("raw", ""))
        m = re.search(r":(\d+)(?::\d+)?:", raw)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        return None

    @staticmethod
    def _extract_issue_message(item: dict) -> str:
        for key in ("message", "desc"):
            value = item.get(key)
            if value:
                return DAAgent._normalize_text(value)
        raw = item.get("raw")
        if raw:
            return DAAgent._normalize_text(raw)
        return ""

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value).replace("\\", "/").lower().strip()

    @staticmethod
    def _normalize_path(value: object) -> str:
        normalized = DAAgent._normalize_text(value)
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.replace("//", "/")
        return normalized

    @staticmethod
    def _normalize_rule(value: object) -> str:
        normalized = DAAgent._normalize_text(value)
        normalized = normalized.replace(" ", "")
        normalized = normalized.replace("::", "/")
        return normalized

    def _run_cmd(self, cmd: str, *, check: bool) -> subprocess.CompletedProcess[str]:
        if self.config.execution_mode == "docker":
            shell_cmd = shlex.quote(cmd)
            docker_cmd = (
                f"docker run --rm "
                f"-v {shlex.quote(str(self.config.repo_path))}:{shlex.quote(self.config.docker_workdir)} "
                f"-w {shlex.quote(self.config.docker_workdir)} "
                f"{self.config.docker_extra_args} "
                f"{shlex.quote(self.config.docker_image)} "
                f"/bin/bash -lc {shell_cmd}"
            )
            run_cmd = docker_cmd
            run_cwd = None
        else:
            run_cmd = cmd
            run_cwd = self.config.repo_path

        proc = subprocess.run(
            run_cmd,
            shell=True,
            cwd=run_cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(f"Command failed: {run_cmd}\n{proc.stdout}")
        return proc

    def _run_cmd_split(self, cmd: str, *, check: bool) -> subprocess.CompletedProcess[str]:
        """Run command capturing stdout and stderr separately (not merged).

        Used by cppcheck: XML output goes to stderr, matching evaluate_in_docker.py
        which reads result.stderr for XML parsing.
        """
        if self.config.execution_mode == "docker":
            shell_cmd = shlex.quote(cmd)
            docker_cmd = (
                f"docker run --rm "
                f"-v {shlex.quote(str(self.config.repo_path))}:{shlex.quote(self.config.docker_workdir)} "
                f"-w {shlex.quote(self.config.docker_workdir)} "
                f"{self.config.docker_extra_args} "
                f"{shlex.quote(self.config.docker_image)} "
                f"/bin/bash -lc {shell_cmd}"
            )
            run_cmd = docker_cmd
            run_cwd = None
        else:
            run_cmd = cmd
            run_cwd = self.config.repo_path

        proc = subprocess.run(
            run_cmd,
            shell=True,
            cwd=run_cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(f"Command failed: {run_cmd}\nstdout={proc.stdout}\nstderr={proc.stderr}")
        return proc
