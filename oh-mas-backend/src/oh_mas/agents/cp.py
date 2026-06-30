from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from minisweagent.models.test_models import make_toolcall_output

from oh_mas.agents.cp_worker import CPMiniWorkerRunner, CPWorkerSpec
from oh_mas.core.schemas import ContextReadyEvent, PatchItem, PatchesReadyEvent, TaskProfiledEvent


@dataclass
class CPConfig:
    provider: str = "litellm"
    backend: str = "litellm"
    worker_model_class: str = "litellm"
    temperature: float = 0.0
    max_tokens: int = 1200
    timeout: int = 60
    fallback_diff: bool = False
    step_limit: int = 10
    # Per-mode step limits. When set, override step_limit for that mode.
    # E.g. {"easy": 12, "medium": 18, "hard": 25}
    step_limits_by_mode: dict = None  # type: ignore[assignment]
    cost_limit: float = 3.0
    worker_trace_root: str = ""
    deterministic_test_mode: bool = False
    max_parallel_workers: int = 3
    repo_root: str = ""
    task_trace_root: str = ""

    def effective_step_limit(self, mode: str) -> int:
        """Return step limit for the given execution mode."""
        if self.step_limits_by_mode:
            limit = self.step_limits_by_mode.get(mode)
            if isinstance(limit, int) and limit > 0:
                return limit
        return self.step_limit


class CPAgent:
    def __init__(self, config: CPConfig | None = None):
        self.config = config or CPConfig()
        self.last_debug: list[dict] = []
        self.last_llm_traces: list[dict] = []
        self._runner = CPMiniWorkerRunner(
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout=self.config.timeout,
            step_limit=self.config.step_limit,
            cost_limit=self.config.cost_limit,
        )

    def generate(self, *, profiled: TaskProfiledEvent, context: ContextReadyEvent) -> PatchesReadyEvent:
        self.last_debug = []
        self.last_llm_traces = []
        model_ids = list(profiled.cp_input.models)
        if not model_ids:
            return PatchesReadyEvent(task_id=profiled.task_id, mode=profiled.mode, patches=[])

        if self.config.provider == "disabled" and not self.config.deterministic_test_mode:
            raise RuntimeError("CP provider=disabled is only valid with deterministic_test_mode=True")

        # Apply per-mode step limit so harder tasks get more budget
        self._runner.step_limit = self.config.effective_step_limit(profiled.mode)

        max_workers = max(1, min(int(self.config.max_parallel_workers), len(model_ids)))
        ordered_results: dict[int, tuple[PatchItem, dict, dict]] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(self._run_worker_by_index, idx, model_id, profiled, context): idx
                for idx, model_id in enumerate(model_ids)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    patch, debug, trace_ref = future.result()
                except Exception as exc:
                    model_id = model_ids[idx]
                    worker_id = f"cpw-{profiled.retry_index}-{idx + 1}"
                    patch_id = f"{profiled.task_id}-{profiled.mode}-p{idx + 1}"
                    patch = PatchItem(patch_id=patch_id, diff="", model_id=model_id)
                    debug = {
                        "worker_id": worker_id,
                        "patch_id": patch_id,
                        "model_id": model_id,
                        "reason": "worker_failed",
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                    trace_ref = {
                        "worker_id": worker_id,
                        "patch_id": patch_id,
                        "model_id": model_id,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                ordered_results[idx] = (patch, debug, trace_ref)

        patches: list[PatchItem] = []
        for idx in sorted(ordered_results.keys()):
            patch, debug, trace_ref = ordered_results[idx]
            patches.append(patch)
            self.last_debug.append(debug)
            self.last_llm_traces.append(trace_ref)

        return PatchesReadyEvent(task_id=profiled.task_id, mode=profiled.mode, patches=patches)

    def _run_worker_by_index(
        self,
        idx: int,
        model_id: str,
        profiled: TaskProfiledEvent,
        context: ContextReadyEvent,
    ) -> tuple[PatchItem, dict, dict]:
        patch_id = f"{profiled.task_id}-{profiled.mode}-p{idx + 1}"
        worker_id = f"cpw-{profiled.retry_index}-{idx + 1}"
        trajectory_path = self._trajectory_path(profiled.task_id, worker_id)
        model_class = self._resolve_worker_model_class(model_id)
        spec = CPWorkerSpec(
            worker_id=worker_id,
            patch_id=patch_id,
            model_id=model_id,
            model_class=model_class,
            trajectory_path=trajectory_path,
        )

        start = perf_counter()
        submission = ""
        worker_debug: dict = {
            "worker_id": worker_id,
            "patch_id": patch_id,
            "model_id": model_id,
            "model_class": model_class,
            "provider": self.config.provider,
            "backend": self.config.backend,
            "agent_class": "CPWorkerAgent",
            "runner_class": "CPMiniWorkerRunner",
            "uses_real_llm": not self.config.deterministic_test_mode,
            "parallel_worker_index": idx,
            "used_fallback": False,
        }
        trace_ref: dict = {
            "trace_backend": "mini-swe-agent",
            "trajectory_path": str(trajectory_path) if trajectory_path else "",
            "trajectory_format": "mini-swe-agent-1.1",
            "worker_id": worker_id,
            "patch_id": patch_id,
            "model_id": model_id,
            "model_class": model_class,
            "agent_class": "CPWorkerAgent",
            "uses_real_llm": not self.config.deterministic_test_mode,
            "llm_trace_embedded_in_trajectory": True,
        }

        try:
            test_outputs = self._deterministic_outputs(profiled) if self.config.deterministic_test_mode else None
            submission, worker_debug, trace_ref = self._runner.run_worker(
                profiled=profiled,
                context=context,
                spec=spec,
                repo_root=self._repo_root(),
                test_outputs=test_outputs,
            )
            worker_debug.setdefault("used_fallback", False)
            worker_debug["provider"] = self.config.provider
            worker_debug["backend"] = self.config.backend
            worker_debug["agent_class"] = worker_debug.get("agent_class", "CPWorkerAgent")
            worker_debug["runner_class"] = worker_debug.get("runner_class", self._runner.__class__.__name__)
            worker_debug["uses_real_llm"] = not self.config.deterministic_test_mode
            worker_debug["parallel_worker_index"] = idx
            worker_debug["reason"] = "worker_submitted_patch" if submission and submission.strip() else "worker_no_submission"
            worker_debug["submission_full"] = submission
            trace_ref["agent_class"] = trace_ref.get("agent_class", "CPWorkerAgent")
            trace_ref["uses_real_llm"] = not self.config.deterministic_test_mode
        except Exception as exc:
            worker_debug["reason"] = "worker_failed"
            worker_debug["error"] = {"type": type(exc).__name__, "message": str(exc)}
            submission = ""
            trace_ref.setdefault("error", {"type": type(exc).__name__, "message": str(exc)})

        duration_ms = round((perf_counter() - start) * 1000, 3)
        worker_debug["duration_ms"] = duration_ms

        diff = submission
        if not diff and self.config.fallback_diff:
            diff = self._fallback_diff(profiled)
            worker_debug["used_fallback"] = True

        return PatchItem(patch_id=patch_id, diff=diff, model_id=model_id), worker_debug, trace_ref

    def _resolve_worker_model_class(self, model_id: str) -> str:
        if "/" in model_id:
            return model_id.split("/", 1)[0]
        return self.config.worker_model_class or self.config.backend or "litellm"

    def _repo_root(self) -> str:
        if self.config.repo_root:
            configured = Path(self.config.repo_root)
            resolved = configured if configured.is_absolute() else (Path.cwd() / configured).resolve()
            if resolved.exists() and resolved.is_dir():
                return str(resolved)
        return str(Path.cwd())

    def _trajectory_path(self, task_id: str, worker_id: str) -> Path | None:
        if self.config.task_trace_root:
            return Path(self.config.task_trace_root) / "agents" / "cp" / f"{worker_id}.traj.json"
        if self.config.worker_trace_root:
            # Backward compatible fallback when orchestrator-level task trace root is not injected.
            root = Path(self.config.worker_trace_root)
            return root / task_id / f"{worker_id}.traj.json"
        return None

    @staticmethod
    def _fallback_diff(profiled: TaskProfiledEvent) -> str:
        return (
            f"--- a/{profiled.alarm.file}\n"
            f"+++ b/{profiled.alarm.file}\n"
            "@@ -1,1 +1,1 @@\n"
            f"-// TODO {profiled.alarm.rule}\n"
            "+// FIXED by OH-MAS CP fallback\n"
        )

    @staticmethod
    def _deterministic_outputs(profiled: TaskProfiledEvent) -> list[dict]:
        patch = (
            f"--- a/{profiled.alarm.file}\n"
            f"+++ b/{profiled.alarm.file}\n"
            "@@ -1,1 +1,1 @@\n"
            f"-// TODO {profiled.alarm.rule}\n"
            "+// FIXED by OH-MAS CP worker deterministic mode\n"
        )
        emit_command = "emit_patch <<'PATCH'\n" + patch + "PATCH"
        return [
            make_toolcall_output(
                "Inspecting context",
                [
                    {
                        "id": "call_show_context",
                        "type": "function",
                        "function": {"name": "bash", "arguments": json.dumps({"command": "show_context"})},
                    }
                ],
                [{"command": "show_context", "tool_call_id": "call_show_context"}],
            ),
            make_toolcall_output(
                "Capturing patch",
                [
                    {
                        "id": "call_emit_patch",
                        "type": "function",
                        "function": {"name": "bash", "arguments": json.dumps({"command": emit_command})},
                    }
                ],
                [{"command": emit_command, "tool_call_id": "call_emit_patch"}],
            ),
            make_toolcall_output(
                "Submitting patch",
                [
                    {
                        "id": "call_submit_patch",
                        "type": "function",
                        "function": {"name": "bash", "arguments": json.dumps({"command": "submit_patch"})},
                    }
                ],
                [{"command": "submit_patch", "tool_call_id": "call_submit_patch"}],
            ),
        ]
