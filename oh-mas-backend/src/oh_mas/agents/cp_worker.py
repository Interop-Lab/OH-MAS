from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from minisweagent.agents.default import AgentConfig, DefaultAgent
from minisweagent.exceptions import Submitted
from minisweagent.models import get_model
from minisweagent.models.test_models import DeterministicToolcallModel
from minisweagent.utils.serialize import recursive_merge

from oh_mas.core.schemas import CPProfileInput, ContextReadyEvent, TaskProfiledEvent


class CPWorkerEnvironmentConfig(BaseModel):
    repo_root: str
    timeout: int = 30


class CPWorkerEnvironment:
    def __init__(self, *, profiled: TaskProfiledEvent, context: ContextReadyEvent, config_class: type = CPWorkerEnvironmentConfig, **kwargs):
        self.config = config_class(**kwargs)
        self.profiled = profiled
        self.context = context
        self.repo_root = Path(self.config.repo_root)
        self._edits_made = False
        self._stored_patch = ""  # 保留向后兼容

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        command = (action.get("command") or "").strip()
        # 兼容shell heredoc patch捕获（cat > /tmp/fix.patch <<EOF）
        self._capture_patch_from_command(command)

        # show_alarm and show_context removed - info is now in instance template directly

        if command.startswith("read_file "):
            target = command[len("read_file ") :].strip()
            return self._read_file(target)

        if command.startswith("search_code "):
            pattern = command[len("search_code ") :].strip()
            return self._search_code(pattern)

        if command.startswith("edit_file "):
            return self._edit_file(command)

        # 保留emit_patch向后兼容（deterministic模式需要）
        if command.startswith("emit_patch"):
            return self._emit_patch(command)

        if command == "submit_patch":
            # 标记提交，实际diff由runner生成
            raise Submitted(
                {
                    "role": "exit",
                    "content": self._stored_patch,  # 兼容旧路径
                    "extra": {"exit_status": "Submitted", "submission": self._stored_patch, "edits_made": self._edits_made},
                }
            )

        if not command:
            return self._error("Empty command.")

        return self._run_shell_command(command, timeout=timeout)

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return recursive_merge(
            self.config.model_dump(),
            {
                "repo_root": str(self.repo_root),
                "alarm": self.profiled.alarm.to_dict(),
                "profiled": CPProfileInput.from_task_profiled(self.profiled).to_worker_dict(),
                "context": self.context.to_worker_dict(),
            },
            kwargs,
        )

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                }
            }
        }

    def _read_file(self, target: str) -> dict[str, Any]:
        path = self.repo_root / target
        if not path.is_file():
            return self._error(f"File not found: {target}")
        return self._ok(path.read_text(encoding="utf-8", errors="replace"))

    def _search_code(self, pattern: str) -> dict[str, Any]:
        if shutil.which("rg"):
            cmd = ["rg", "-n", pattern, str(self.repo_root)]
        else:
            cmd = ["grep", "-R", "-n", "-I", "-E", pattern, str(self.repo_root)]
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            return {"output": str(exc), "returncode": 127, "exception_info": str(exc)}
        return {"output": proc.stdout[:12000], "returncode": proc.returncode, "exception_info": ""}

    def _edit_file(self, command: str) -> dict[str, Any]:
        """
        Edit file using JSON parameters.
        Format: edit_file {"path": "...", "old_str": "...", "new_str": "..."}
        """
        try:
            json_start = command.find("{")
            if json_start == -1:
                return self._error(
                    "Invalid edit_file format. Expected: edit_file {\"path\": \"...\", \"old_str\": \"...\", \"new_str\": \"...\"}"
                )

            json_str = command[json_start:]
            params = json.loads(json_str)

            path_str = params.get("path", "").strip()
            old_str = params.get("old_str", "")
            new_str = params.get("new_str", "")

            if not path_str:
                return self._error("Missing 'path' parameter")

            # 路径安全检查：防止越界
            file_path = self.repo_root / path_str
            try:
                resolved = file_path.resolve()
                if not resolved.is_relative_to(self.repo_root.resolve()):
                    return self._error(f"Path escapes repo root: {path_str}")
            except (ValueError, RuntimeError):
                return self._error(f"Invalid path: {path_str}")

            if not file_path.is_file():
                return self._error(f"File not found: {path_str}")

            content = file_path.read_text(encoding="utf-8")

            if old_str and old_str not in content:
                return self._error(
                    f"String not found in {path_str}\n"
                    f"Hint: Verify indentation and exact match. First 100 chars of old_str: {old_str[:100]!r}"
                )

            if old_str:
                occurrence_count = content.count(old_str)
                if occurrence_count > 1:
                    return self._error(
                        f"Found {occurrence_count} occurrences in {path_str}\n"
                        f"Hint: Add more surrounding context to make the match unique."
                    )

            if old_str:
                new_content = content.replace(old_str, new_str, 1)
            else:
                new_content = content + new_str

            file_path.write_text(new_content, encoding="utf-8")
            self._edits_made = True

            delta = len(new_str) - len(old_str)
            return self._ok(
                f"✓ Edited {path_str}\n"
                f"  Matched: {len(old_str)} chars\n"
                f"  Replaced with: {len(new_str)} chars (delta: {delta:+d})\n"
                f"  File modified in worktree. Call submit_patch to generate final diff."
            )

        except json.JSONDecodeError as e:
            return self._error(f"Invalid JSON: {e}")
        except Exception as e:
            return self._error(f"edit_file failed: {type(e).__name__}: {e}")

    def _emit_patch(self, command: str) -> dict[str, Any]:
        """保留emit_patch支持旧路径（deterministic模式）"""
        prefix = "emit_patch"
        payload = command[len(prefix) :].lstrip()
        patch = payload
        if payload.startswith("<<"):
            lines = payload.splitlines()
            if not lines:
                return self._error("emit_patch heredoc missing payload")
            marker = lines[0][2:].strip().strip("'\"")
            body = []
            for line in lines[1:]:
                if line.strip() == marker:
                    break
                body.append(line)
            patch = "\n".join(body)
        self._stored_patch = self._normalize_patch_text(patch)
        return self._ok(f"PATCH_CAPTURED {len(self._stored_patch)}")

    def _run_shell_command(self, command: str, *, timeout: int | None = None) -> dict[str, Any]:
        effective_timeout = timeout if timeout is not None else self.config.timeout
        try:
            proc = subprocess.run(
                ["bash", "-lc", command],
                cwd=str(self.repo_root),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                check=False,
            )
        except Exception as exc:
            return {"output": str(exc), "returncode": 1, "exception_info": str(exc)}
        output = proc.stdout[:12000]
        # 兼容shell heredoc patch捕获（cat > /tmp/fix.patch <<EOF）
        self._capture_patch_from_text(output)
        return {"output": output, "returncode": proc.returncode, "exception_info": ""}

    def has_edits(self) -> bool:
        return self._edits_made

    def get_stored_patch(self) -> str:
        """保留向后兼容"""
        return self._stored_patch

    def _capture_patch_from_command(self, command: str) -> None:
        """从命令文本中提取unified diff"""
        patch = self._extract_unified_diff(command)
        if patch:
            self._stored_patch = self._normalize_patch_text(patch)

    def _capture_patch_from_text(self, text: str) -> None:
        """从输出文本中提取unified diff"""
        patch = self._extract_unified_diff(text)
        if patch:
            self._stored_patch = self._normalize_patch_text(patch)

    @staticmethod
    def _extract_unified_diff(text: str) -> str:
        """提取unified diff格式的patch"""
        if not text:
            return ""
        lines = text.splitlines()
        start_idx: int | None = None
        for i in range(len(lines) - 1):
            if lines[i].startswith("--- ") and lines[i + 1].startswith("+++ "):
                start_idx = i
                break
        if start_idx is None:
            return ""
        if not any(line.startswith("@@ ") for line in lines[start_idx:]):
            return ""
        end_idx = len(lines)
        for i in range(start_idx + 2, len(lines)):
            if lines[i].strip() == "EOF":
                end_idx = i
                break
        return "\n".join(lines[start_idx:end_idx])

    @staticmethod
    def _normalize_patch_text(patch: str) -> str:
        """Normalize patch line endings without stripping meaningful whitespace."""
        if not patch:
            return ""
        normalized = patch.replace("\r\n", "\n").replace("\r", "")
        if not normalized.endswith("\n"):
            normalized += "\n"
        return normalized

    @staticmethod
    def _ok(output: str) -> dict[str, Any]:
        return {"output": output, "returncode": 0, "exception_info": ""}

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"output": message, "returncode": 1, "exception_info": message}


class CPWorkerAgentConfig(AgentConfig):
    pass


class CPWorkerAgent(DefaultAgent):
    def __init__(self, *args, worker_meta: dict[str, Any] | None = None, template_filters: dict | None = None, **kwargs):
        # Extract template_filters before passing to super (it doesn't recognize it)
        self._custom_filters = template_filters or {}
        super().__init__(*args, config_class=CPWorkerAgentConfig, **kwargs)
        self.worker_meta = worker_meta or {}

    def _render_template(self, template: str) -> str:
        """Override template rendering to inject custom filters.

        This method extends the base _render_template by adding custom Jinja2
        filters for structured prompt formatting.

        Args:
            template: Template string to render (receives template vars internally)

        Returns:
            Rendered template string
        """
        from jinja2 import Environment, StrictUndefined

        # Get template variables from environment (same as parent class)
        template_vars = self.env.get_template_vars() if hasattr(self.env, 'get_template_vars') else {}

        env = Environment(undefined=StrictUndefined, autoescape=False)

        # Register custom filters
        for filter_name, filter_func in self._custom_filters.items():
            env.filters[filter_name] = filter_func

        # Register default filters (tojson for backward compatibility)
        import json
        env.filters['tojson'] = lambda x: json.dumps(x, indent=2, ensure_ascii=False)

        jinja_template = env.from_string(template)
        return jinja_template.render(**template_vars)

    def query(self) -> dict:
        # 保留旧逻辑：提前提交stored_patch（兼容性）
        patch = self._current_patch()
        step_limit_reached = 0 < self.config.step_limit <= self.n_calls
        cost_limit_reached = 0 < self.config.cost_limit <= self.cost
        if patch and (step_limit_reached or cost_limit_reached):
            raise Submitted(
                {
                    "role": "exit",
                    "content": patch,
                    "extra": {"exit_status": "Submitted", "submission": patch},
                }
            )
        return super().query()

    def _current_patch(self) -> str:
        getter = getattr(self.env, "get_stored_patch", None)
        if callable(getter):
            return getter() or ""
        return ""

    def serialize(self, *extra_dicts) -> dict:
        return super().serialize({"info": {"oh_mas_worker": self.worker_meta}}, *extra_dicts)


@dataclass
class CPWorkerSpec:
    worker_id: str
    patch_id: str
    model_id: str
    model_class: str
    trajectory_path: Path | None


class CPMiniWorkerRunner:
    def __init__(self, *, temperature: float, max_tokens: int, timeout: int, step_limit: int, cost_limit: float):
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.step_limit = step_limit
        self.cost_limit = cost_limit

    def run_worker(
        self,
        *,
        profiled: TaskProfiledEvent,
        context: ContextReadyEvent,
        spec: CPWorkerSpec,
        repo_root: str,
        test_outputs: list[dict] | None = None,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        worktree_path = None
        cleanup_notes = []
        use_worktree = True  # 默认启用worktree

        # 初始化trajectory引用（确保失败时也有）
        trajectory_ref = {
            "trace_backend": "mini-swe-agent",
            "trajectory_path": str(spec.trajectory_path) if spec.trajectory_path else "",
            "trajectory_format": "mini-swe-agent-1.1",
            "worker_id": spec.worker_id,
            "patch_id": spec.patch_id,
            "model_id": spec.model_id,
            "model_class": spec.model_class,
            "agent_class": f"{CPWorkerAgent.__module__}.{CPWorkerAgent.__name__}",
            "environment_class": f"{CPWorkerEnvironment.__module__}.{CPWorkerEnvironment.__name__}",
            "llm_request_recorded_in_trajectory": True,
            "llm_trace_embedded_in_trajectory": True,
            "worktree_used": False,
            "worktree_fallback": False,
            "edits_made_via_edit_file": False,
        }

        worker_debug = {
            "worker_id": spec.worker_id,
            "patch_id": spec.patch_id,
            "model_id": spec.model_id,
            "model_class": spec.model_class,
            "agent_class": f"{CPWorkerAgent.__module__}.{CPWorkerAgent.__name__}",
            "environment_class": f"{CPWorkerEnvironment.__module__}.{CPWorkerEnvironment.__name__}",
            "submission_preview": "",
            "submission_full": "",
            "trajectory_path": str(spec.trajectory_path) if spec.trajectory_path else "",
            "trajectory_message_count": 0,
            "worktree_notes": [],
        }

        try:
            # 尝试创建worktree
            if use_worktree:
                try:
                    worktree_path, create_msg = self._create_worker_worktree(repo_root, spec.worker_id)
                    cleanup_notes.append(create_msg)
                    trajectory_ref["worktree_used"] = True
                    effective_repo_root = worktree_path
                except Exception as e:
                    # 降级：创建临时副本目录（保持隔离）
                    import tempfile
                    temp_clone = tempfile.mkdtemp(prefix=f"cp_worker_{spec.worker_id}_")
                    shutil.copytree(repo_root, temp_clone, dirs_exist_ok=True)
                    worktree_path = temp_clone  # 标记为需要清理
                    cleanup_notes.append(f"worktree_failed_use_temp_clone: {type(e).__name__}: {str(e)[:100]}")
                    trajectory_ref["worktree_fallback"] = True
                    trajectory_ref["worktree_fallback_reason"] = f"{type(e).__name__}: {str(e)[:200]}"
                    effective_repo_root = temp_clone
                    use_worktree = False  # 标记不使用git diff（临时副本没有git状态）
            else:
                effective_repo_root = repo_root

            model = self._build_model(spec, test_outputs)
            env = CPWorkerEnvironment(
                profiled=profiled,
                context=context,
                repo_root=effective_repo_root,
                timeout=self.timeout
            )
            agent = CPWorkerAgent(
                model,
                env,
                worker_meta={
                    "worker_id": spec.worker_id,
                    "patch_id": spec.patch_id,
                    "model_id": spec.model_id,
                    "model_class": spec.model_class,
                },
                system_template=self._build_system_template(),
                instance_template=self._build_instance_template(),
                step_limit=self.step_limit,
                cost_limit=self.cost_limit,
                output_path=spec.trajectory_path,
                template_filters=self._build_template_filters(),
            )

            # task_payload is now minimal - alarm, context, and profiled are in template vars
            # We pass task_id for identification only
            task_payload = profiled.task_id

            result = agent.run(task_payload)

            # 生成diff：从worktree或temp clone（都支持git diff），降级使用stored_patch
            if worktree_path:
                submission = self._generate_diff_from_worktree(worktree_path)
                if not submission.strip():
                    # Git diff无修改，回退到stored_patch
                    submission = env.get_stored_patch()
                    cleanup_notes.append("no_git_changes_fallback_to_stored_patch")
            else:
                # 降级路径：使用stored_patch（不应该到这里）
                submission = env.get_stored_patch()

            trajectory_ref["edits_made_via_edit_file"] = env.has_edits()

            # 保存trajectory（确保完整性）
            trajectory_data = agent.save(None)
            trajectory_ref["trajectory_data"] = trajectory_data

            worker_debug.update({
                "submission_preview": submission[:500],
                "submission_full": submission,
                "trajectory_message_count": len(trajectory_data.get("messages", [])),
                "worktree_path": worktree_path if use_worktree else "",
                "worktree_notes": cleanup_notes,
                "edits_made_via_edit_file": env.has_edits(),
                "patch_generated_by": "git_diff" if (use_worktree and submission) else "stored_patch",
            })

            return submission, worker_debug, trajectory_ref

        except Exception as e:
            # 确保失败时也有trajectory
            cleanup_notes.append(f"worker_exception: {type(e).__name__}: {str(e)[:200]}")
            worker_debug["worktree_notes"] = cleanup_notes
            worker_debug["error"] = {"type": type(e).__name__, "message": str(e)}
            trajectory_ref["error"] = {"type": type(e).__name__, "message": str(e)}

            # 尝试保存已有trajectory
            try:
                if 'agent' in locals():
                    trajectory_data = agent.save(None)
                    trajectory_ref["trajectory_data"] = trajectory_data
                    worker_debug["trajectory_message_count"] = len(trajectory_data.get("messages", []))
            except Exception:
                pass

            raise

        finally:
            if worktree_path:
                cleanup_msg = self._cleanup_worker_worktree(repo_root, worktree_path)
                cleanup_notes.append(cleanup_msg)
                if "worktree_notes" in worker_debug:
                    worker_debug["worktree_notes"] = cleanup_notes

    def _create_worker_worktree(self, repo_root: str, worker_id: str) -> tuple[str, str]:
        """创建worktree，带重试机制"""
        # 添加safe.directory配置（修复Docker容器中的dubious ownership问题）
        try:
            subprocess.run(
                ["git", "config", "--global", "--add", "safe.directory", repo_root],
                capture_output=True,
                timeout=5,
                check=False  # 如果已存在也不报错
            )
        except Exception:
            pass  # 配置失败不影响后续流程（会依赖fallback）

        worktree_root = Path(repo_root).parent / "worktrees"
        worktree_root.mkdir(parents=True, exist_ok=True)
        worktree_path = worktree_root / worker_id

        if worktree_path.exists():
            try:
                subprocess.run(
                    ["git", "-C", repo_root, "worktree", "remove", "--force", str(worktree_path)],
                    capture_output=True,
                    timeout=10,
                    check=False
                )
            except Exception:
                pass
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = subprocess.run(
                    ["git", "-C", repo_root, "worktree", "add", "--detach", str(worktree_path), "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True
                )
                return str(worktree_path), f"worktree_created_attempt_{attempt + 1}"

            except subprocess.CalledProcessError as e:
                if attempt < max_retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                else:
                    raise RuntimeError(
                        f"Failed to create worktree after {max_retries} attempts: {e.stderr}"
                    )
            except Exception as e:
                raise RuntimeError(f"Worktree creation failed: {e}")

    def _cleanup_worker_worktree(self, repo_root: str, worktree_path: str) -> str:
        """清理worktree"""
        try:
            result = subprocess.run(
                ["git", "-C", repo_root, "worktree", "remove", "--force", worktree_path],
                capture_output=True,
                text=True,
                timeout=10,
                check=False
            )

            if result.returncode == 0:
                subprocess.run(
                    ["git", "-C", repo_root, "worktree", "prune"],
                    capture_output=True,
                    timeout=5,
                    check=False
                )
                return "worktree_cleaned"
            else:
                if Path(worktree_path).exists():
                    shutil.rmtree(worktree_path, ignore_errors=True)
                subprocess.run(
                    ["git", "-C", repo_root, "worktree", "prune"],
                    capture_output=True,
                    timeout=5,
                    check=False
                )
                return f"worktree_force_cleaned: {result.stderr[:100]}"

        except Exception as e:
            try:
                if Path(worktree_path).exists():
                    shutil.rmtree(worktree_path, ignore_errors=True)
                subprocess.run(
                    ["git", "-C", repo_root, "worktree", "prune"],
                    capture_output=True,
                    timeout=5,
                    check=False
                )
            except Exception:
                pass
            return f"worktree_cleanup_error: {type(e).__name__}"

    def _generate_diff_from_worktree(self, worktree_path: str) -> str:
        """从worktree生成diff"""
        try:
            result = subprocess.run(
                ["git", "-C", worktree_path, "diff", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True
            )
            # Do NOT use strip() - it removes trailing context lines required by unified diff format
            # Only strip Windows-style line endings if needed
            return result.stdout.rstrip('\r') if result.stdout else ""
        except Exception:
            return ""

    def _build_template_filters(self) -> dict:
        """Build custom Jinja2 filters for structured prompt rendering.

        Returns:
            Dictionary of filter name -> filter function mappings
        """
        from oh_mas.agents import cp_prompt_formatter
        from oh_mas.agents.cp_worker_rules import get_rule_instructions

        def format_alarm(alarm_dict):
            """Convert alarm dict to Alarm and format."""
            from oh_mas.core.schemas import Alarm
            if isinstance(alarm_dict, dict):
                alarm = Alarm(**alarm_dict)
            else:
                alarm = alarm_dict
            return cp_prompt_formatter.format_alarm_section(alarm)

        def format_context(context_dict):
            """Convert context dict to ContextReadyEvent and format."""
            from oh_mas.core.schemas import ContextReadyEvent
            if isinstance(context_dict, dict):
                # Reconstruct from worker dict representation
                # Note: context in template vars is already transformed via to_worker_dict()
                # So we format the dict directly
                return self._format_context_from_dict(context_dict)
            else:
                return cp_prompt_formatter.format_context_section(context_dict)

        def format_retry(profiled_dict):
            """Conditionally format retry protocol (only if retry_index > 0)."""
            retry_index = profiled_dict.get("retry_index", 0)
            if retry_index == 0:
                return ""  # First attempt - no retry protocol

            # Reconstruct objects for formatting
            from oh_mas.core.schemas import PreviousAuditFeedback, TaskProfiledEvent
            previous_feedback_dict = profiled_dict.get("previous_audit_feedback")
            if not previous_feedback_dict:
                return ""

            # Build feedback object from dict
            previous_feedback = self._build_feedback_from_dict(previous_feedback_dict)

            # Build minimal profiled event for formatting
            profiled = self._build_profiled_from_dict(profiled_dict)

            return cp_prompt_formatter.format_retry_protocol(profiled, previous_feedback)

        def format_rule_guide(alarm_dict):
            """Dynamically load rule-specific instructions based on alarm rule."""
            if isinstance(alarm_dict, dict):
                rule = alarm_dict.get("rule", "")
            else:
                rule = alarm_dict.rule
            return get_rule_instructions(rule)

        def format_workflow(alarm_dict):
            """Format workflow hint with step budget."""
            if isinstance(alarm_dict, dict):
                file = alarm_dict.get("file", "")
            else:
                file = alarm_dict.file
            return cp_prompt_formatter.format_workflow_hint(self.step_limit, file)

        return {
            "format_alarm": format_alarm,
            "format_context": format_context,
            "format_retry": format_retry,
            "format_rule_guide": format_rule_guide,
            "format_workflow": format_workflow,
        }

    def _format_context_from_dict(self, context_dict: dict) -> str:
        """Format GW context for CP Worker.

        Dispatches on context_mode:
        - graph_centric (easy):  alarm-centric dependency graph — file list + edges
        - precise (medium/hard): repair_contract triplet from GW synthesis
        """
        context_mode = context_dict.get("context_mode", "unknown")

        if context_mode == "graph_centric":
            return self._format_graph_centric(context_dict)

        # precise (medium/hard) or unknown fallback
        return self._format_repair_contract(context_dict)

    def _format_graph_centric(self, context_dict: dict) -> str:
        """Render alarm-centric dependency graph for easy-mode CP."""
        sections = ["# Dependency Graph Context (easy mode)\n"]
        sections.append(
            "The following files are relevant to the alarm based on static dependency analysis.\n"
            "Read the alarm file first, then inspect neighbors as needed.\n"
        )

        relevant_files = context_dict.get("relevant_files", [])
        if relevant_files:
            sections.append("## Relevant Files")
            for f in relevant_files:
                sections.append(f"- {f}")
            sections.append("")

        edges = context_dict.get("dependency_edges", [])
        if edges:
            sections.append("## Dependency Edges")
            for e in edges:
                sections.append(f"- {e}")
            sections.append("")

        return "\n".join(sections)

    def _format_repair_contract(self, context_dict: dict) -> str:
        """Render GW repair contract triplet for medium/hard-mode CP."""
        sections = ["# Repair Contract from GW Agent\n"]

        contract = context_dict.get("repair_contract", {})
        must_fix = contract.get("must_fix", [])
        must_not_touch = contract.get("must_not_touch", [])
        allowed_tx = contract.get("allowed_transformations", [])

        if not must_fix and not must_not_touch and not allowed_tx:
            sections.append(
                "*No repair contract available. "
                "Use alarm details to determine the fix.*\n"
            )
        else:
            if must_fix:
                sections.append("## Must Fix")
                sections.append("These locations/patterns MUST be addressed to eliminate the alarm(s):\n")
                for item in must_fix:
                    sections.append(f"- {item}")
                sections.append("")

            if must_not_touch:
                sections.append("## Must Not Touch")
                sections.append("These elements MUST NOT be modified to avoid regressions:\n")
                for item in must_not_touch:
                    sections.append(f"- {item}")
                sections.append("")

            if allowed_tx:
                sections.append("## Allowed Transformations")
                sections.append("Apply ONE of these concrete transformation patterns:\n")
                for item in allowed_tx:
                    sections.append(f"- {item}")
                sections.append("")

        reasoning = context_dict.get("reasoning", "")
        if reasoning:
            sections.append("## GW Derivation Rationale")
            sections.append(reasoning)
            sections.append("")

        return "\n".join(sections)

    def _build_feedback_from_dict(self, feedback_dict: dict):
        """Reconstruct PreviousAuditFeedback from dict."""
        from oh_mas.core.schemas import PatchDiagnostic, PreviousAuditFeedback, IntroducedWarning

        diagnostics = []
        for diag_dict in feedback_dict.get("patch_diagnostics", []):
            warnings = [
                IntroducedWarning(**w) for w in diag_dict.get("introduced_warnings", [])
            ]
            diagnostics.append(
                PatchDiagnostic(
                    patch_id=diag_dict.get("patch_id", ""),
                    failed_level=diag_dict.get("failed_level", "L1"),
                    reason=diag_dict.get("reason", ""),
                    tool=diag_dict.get("tool", ""),
                    details=diag_dict.get("details", ""),
                    introduced_warnings=warnings,
                )
            )

        return PreviousAuditFeedback(
            failed_level=feedback_dict.get("failed_level", "L1"),
            reason=feedback_dict.get("reason", ""),
            failed_patches=feedback_dict.get("failed_patches", []),
            patch_diagnostics=diagnostics,
        )

    def _build_profiled_from_dict(self, profiled_dict: dict):
        """Build minimal TaskProfiledEvent for retry formatting."""
        from oh_mas.core.schemas import TaskProfiledEvent, Alarm, GWInput, CPInput

        # Minimal reconstruction - only fields needed for retry protocol
        return TaskProfiledEvent(
            task_id=profiled_dict.get("task_id", ""),
            retry_index=profiled_dict.get("retry_index", 0),
            mode=profiled_dict.get("mode", "easy"),
            alarm=Alarm(**profiled_dict.get("alarm", {})),
            gw_input=GWInput(build_semantic_graph=False, extract_constraints=False),
            cp_input=CPInput(model_count=0, models=[]),
            previous_audit_feedback=None,  # Will be set by caller
        )

    def _build_system_template(self) -> str:
        """Build streamlined system template focused on core protocol.

        This template is kept minimal to reduce token overhead. Retry-specific
        instructions and rule-specific guidance are injected in instance template.
        """
        return (
            "You are a CP (Constrained Patcher) worker in OH-MAS, specialized in generating "
            "precise code patches for OpenHarmony linter violations.\n\n"

            "# Mission\n"
            "Analyze linter alarms and produce high-quality patches that:\n"
            "1. Fix the target violation without introducing new warnings\n"
            "2. Preserve code style and framework conventions\n"
            "3. Apply cleanly with `git apply`\n\n"

            "# Core Constraints\n"
            f"- **Step budget:** {self.step_limit} steps (HARD LIMIT)\n"
            "- **Workspace:** Isolated git worktree (changes don't affect main repo)\n"
            "- **Patch generation:** System auto-generates unified diff from your edits\n\n"

            "# Available Commands\n"
            "Execute these via the bash tool:\n\n"

            "**1. read_file <path>**\n"
            "   Read source code (path relative to repo root)\n"
            "   Example: `read_file entry/src/main/ets/pages/Index.ets`\n\n"

            "**2. search_code <pattern>**\n"
            "   Search codebase with ripgrep/grep\n"
            "   Example: `search_code 'class.*Component'`\n\n"

            "**3. edit_file <json>**\n"
            "   Modify file using JSON parameters\n"
            "   Format: `edit_file {\"path\": \"...\", \"old_str\": \"...\", \"new_str\": \"...\"}`\n"
            "   - `path`: File path relative to repo root\n"
            "   - `old_str`: Exact string to find (must be unique in file)\n"
            "   - `new_str`: Replacement string\n"
            "   - Include enough context in old_str to ensure uniqueness\n"
            "   - Preserve exact indentation and whitespace\n\n"

            "**4. submit_patch**\n"
            "   Generate unified diff from your edits and submit\n\n"

            "# Editing Protocol\n"
            "- Always use `read_file` before editing to get current content\n"
            "- Never reuse old_str from memory - copy exact strings from read_file output\n"
            "- Ensure old_str is unique within the target file\n"
            "- Use JSON format to avoid parsing issues with code content\n"
            "- Multiple edit_file calls are allowed for multi-file patches\n"
            f"- Reserve at least 2 steps for editing + submission\n\n"

            "# Example Workflow\n"
            "```bash\n"
            "# Step 1: Read target file\n"
            "read_file src/Component.ets\n\n"

            "# Step 2: Make edit (copy old_str from read_file output)\n"
            'edit_file {"path": "src/Component.ets", "old_str": "  oldCode() {\\n    // ...\\n  }", "new_str": "  newCode() {\\n    // ...\\n  }"}\n\n'

            "# Step 3: Submit\n"
            "submit_patch\n"
            "```\n\n"

            "# Important Reminders\n"
            "- Do NOT write unified diff format manually - use edit_file instead\n"
            "- When you see '=== MANDATORY CONSTRAINTS ===' in task payload, follow them exactly\n"
            "- Use the repair contract (Must Fix / Allowed Transformations) as primary repair guide\n"
            f"- If you exceed {self.step_limit} steps, system submits whatever diff exists\n"
        )

    def _build_instance_template(self) -> str:
        """Build dynamic instance template using structured formatters.

        The template uses custom Jinja2 filters to render structured data:
        - format_alarm: Renders Alarm object as structured section
        - format_context: Renders GW context (graph_centric for easy / repair_contract for medium/hard)
        - format_retry: Conditionally renders retry EXECUTION PROTOCOL (includes AO's MANDATORY CONSTRAINTS)
        - format_rule_guide: Dynamically loads rule-specific instructions
        - format_workflow: Renders workflow hint (only for first attempt, mutually exclusive with format_retry)

        NOTE: knowledge_pack is consumed by GW to synthesize the repair_contract; it is NOT passed
        directly to CP. In retry scenarios, AO injects MANDATORY CONSTRAINTS via format_retry.
        """
        return (
            "{{alarm | format_alarm}}\n\n"
            "{{context | format_context}}\n\n"
            "{{profiled | format_retry}}\n\n"  # Retry: MANDATORY CONSTRAINTS + execution workflow
            "{{alarm | format_rule_guide}}\n\n"
            "{% if profiled.get('retry_index', 0) == 0 %}"  # Conditional: only for first attempt
            "{{alarm | format_workflow}}\n\n"
            "{% endif %}"
            "# Mission Statement\n"
            f"Analyze the alarm above and produce ONE high-quality patch within {self.step_limit} steps.\n\n"
            "# Pre-Edit Checklist\n"
            "Before calling edit_file, ensure:\n"
            "- [ ] You understand the root cause from alarm + repair contract (or dependency graph)\n"
            "- [ ] You have read relevant files with read_file (never trust memory)\n"
            "- [ ] Your old_str is copied from actual file content (not reconstructed)\n"
            "- [ ] Your old_str is unique in the target file\n"
            "- [ ] Your new_str follows project code style\n"
            "- [ ] If this is a retry, you've followed the RETRY EXECUTION PROTOCOL above\n"
        )

    def _build_model(self, spec: CPWorkerSpec, test_outputs: list[dict] | None):
        if test_outputs is not None:
            return DeterministicToolcallModel(outputs=test_outputs)
        return get_model(
            config={
                "model_class": spec.model_class,
                "model_name": spec.model_id,
                "model_kwargs": {
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "timeout": self.timeout,
                },
            }
        )
