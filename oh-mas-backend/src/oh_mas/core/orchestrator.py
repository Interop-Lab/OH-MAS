from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from oh_mas.agents.ao import AOAgent
from oh_mas.agents.cp import CPAgent
from oh_mas.agents.da import DAAgent
from oh_mas.agents.gw import GWAgent
from oh_mas.core.event_bus import InMemoryEventBus
from oh_mas.core.schemas import Alarm, AuditDoneEvent, CPProfileInput, GWProfileInput, parse_diff_by_file
from oh_mas.core.tracing import TraceRecorder, to_jsonable


@dataclass
class RunResult:
    task_id: str
    final_status: str
    mode_history: list[str]
    final_event: dict
    cp_debug_history: list[dict] = field(default_factory=list)
    trace_entries: list[dict] = field(default_factory=list)
    llm_traces: list[dict] = field(default_factory=list)
    model_patch_output: dict = field(default_factory=dict)  # {"instance_id": ..., "model_patch": {...}}


class OHMASOrchestrator:
    def __init__(
        self,
        ao: AOAgent,
        gw: GWAgent,
        cp: CPAgent,
        da: DAAgent,
        *,
        trace_root: Path | None = None,
        max_retries: int = 2,
    ):
        self.ao = ao
        self.gw = gw
        self.cp = cp
        self.da = da
        self.trace_root = trace_root
        self.max_retries = max_retries

    def run(self, *, task_id: str, alarm: Alarm) -> RunResult:
        mode_history: list[str] = []
        cp_debug_history: list[dict] = []
        llm_traces: list[dict] = []
        trace_dir = self.trace_root / task_id if self.trace_root is not None else None
        recorder = TraceRecorder(task_id=task_id, trace_root=trace_dir)
        bus = InMemoryEventBus()
        state: dict[str, Any] = {
            "current_retry": 0,
            "current_profiled": None,
            "current_context": None,
            "last_failed_audit": None,
            "final_audit": None,
            "patches_by_id": {},  # Store patch diffs by patch_id for retrieval when passing
            "last_task_append": "",  # Carry previous round's constraints for accumulation
        }

        recorder.record("task.started", {"task_id": task_id, "alarm": alarm.to_dict()})

        if trace_dir is not None and hasattr(self.cp, "config"):
            # Inject per-task trace directory so CP worker trajectories co-locate with task traces.
            self.cp.config.task_trace_root = str(trace_dir)

        def read_worker_trajectory(trace: dict[str, Any]) -> dict[str, Any] | None:
            if isinstance(trace.get("trajectory_data"), dict):
                return to_jsonable(trace["trajectory_data"])
            trajectory_path = trace.get("trajectory_path")
            if not trajectory_path:
                return None
            try:
                import json

                path = Path(str(trajectory_path))
                if not path.exists():
                    return None
                return to_jsonable(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                return {"error": {"type": type(exc).__name__, "message": str(exc)}}

        def to_full_llm_trace(trace: dict[str, Any]) -> dict[str, Any]:
            payload = to_jsonable(trace)
            if payload.get("trace_backend") == "mini-swe-agent":
                payload["trajectory"] = read_worker_trajectory(payload)
            return payload

        # Store the original commit hash to reset repo between retry attempts
        original_commit_hash: str | None = None
        if hasattr(self.da, "config") and self.da.config.repo_path.exists():
            try:
                import subprocess
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.da.config.repo_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                original_commit_hash = result.stdout.strip()
            except Exception:
                original_commit_hash = None

        def publish(source: str, event_obj: Any) -> None:
            payload = event_obj.to_dict() if hasattr(event_obj, "to_dict") else to_jsonable(event_obj)
            recorder.record(
                "event.published",
                {
                    "source": source,
                    "event": payload.get("event", "unknown"),
                    "payload": payload,
                },
            )
            bus.publish(payload)

        def record_agent_call(agent_name: str, fn, *, input_payload: dict):
            recorder.record("agent.call.started", {"agent": agent_name, "input": to_jsonable(input_payload)})
            start = perf_counter()
            try:
                result = fn()
                recorder.record(
                    "agent.call.finished",
                    {
                        "agent": agent_name,
                        "input": to_jsonable(input_payload),
                        "output": to_jsonable(result),
                        "duration_ms": round((perf_counter() - start) * 1000, 3),
                    },
                )
                return result
            except Exception as exc:
                recorder.record(
                    "agent.call.failed",
                    {
                        "agent": agent_name,
                        "input": to_jsonable(input_payload),
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                        "duration_ms": round((perf_counter() - start) * 1000, 3),
                    },
                )
                raise

        def on_task_profiled(event: dict[str, Any]) -> None:
            profiled = state["current_profiled"]
            if profiled is None or profiled.task_id != event.get("task_id"):
                return
            # Project profiled event to GW-specific input.
            # previous_audit_feedback is forwarded so GW can reason about what
            # went wrong in the previous attempt and generate a targeted contract.
            gw_input = GWProfileInput(
                task_id=profiled.task_id,
                mode=profiled.mode,
                alarm=profiled.alarm,
                gw_input=profiled.gw_input,
                previous_audit_feedback=profiled.previous_audit_feedback,
            )
            gw_input_payload = gw_input.to_dict()
            gw_trajectory_path = ""
            if trace_dir is not None:
                gw_trajectory_path = str(trace_dir / "agents" / "gw" / f"gw-{profiled.retry_index}.traj.json")
            context = record_agent_call(
                "GW",
                lambda: self.gw.build_context(
                    gw_input,
                    knowledge_pack=profiled.cp_input.knowledge_pack,
                    trajectory_path=gw_trajectory_path or None,
                ),
                input_payload=gw_input_payload,
            )
            # Consolidate GW agent steps into a single trace entry (similar to CP's approach)
            # This avoids bloating trace.jsonl with one seq per step
            gw_trace_steps = getattr(self.gw, "last_trace_steps", [])
            gw_debug = getattr(self.gw, "last_debug", {})
            gw_llm_traces = [to_jsonable(trace) for trace in getattr(self.gw, "last_llm_traces", [])]
            if gw_trace_steps or gw_debug:
                recorder.record("gw.agent.summary", {
                    "bfs_slice": gw_debug.get("bfs_slice"),
                    "agent_summary": gw_debug.get("agent"),
                    "step_count": len(gw_trace_steps),
                    "llm_call_count": len(gw_llm_traces),
                    "trajectory_path": gw_trajectory_path,
                    "llm_trace_embedded_in_trajectory": bool(gw_trajectory_path and gw_llm_traces),
                    "steps": gw_trace_steps,  # All tool steps in one record
                })
            if gw_llm_traces:
                llm_traces.append({
                    "trace_backend": "gw-agent",
                    "agent": "GW",
                    "worker_id": "gw",
                    "trajectory_path": gw_trajectory_path,
                    "llm_call_count": len(gw_llm_traces),
                    "llm_trace_embedded_in_trajectory": bool(gw_trajectory_path),
                    "trajectory": read_worker_trajectory({"trajectory_path": gw_trajectory_path}) if gw_trajectory_path else None,
                })
            state["current_context"] = context
            publish("GW", context)

        def on_context_ready(event: dict[str, Any]) -> None:
            profiled = state["current_profiled"]
            context = state["current_context"]
            if profiled is None or context is None:
                return
            patches = record_agent_call(
                "CP",
                lambda: self.cp.generate(profiled=profiled, context=context),
                input_payload={
                    "profiled": CPProfileInput.from_task_profiled(profiled).to_dict(),
                    "context": context.to_dict(),
                },
            )
            cp_debug_history.append(
                {
                    "retry_index": profiled.retry_index,
                    "mode": profiled.mode,
                    "patches": to_jsonable(self.cp.last_debug),
                }
            )
            for trace in self.cp.last_llm_traces:
                full_payload = to_full_llm_trace(to_jsonable(trace))
                llm_traces.append(full_payload)
                recorder.record("llm.call", full_payload)
            publish("CP", patches)

        def on_patches_ready(event: dict[str, Any]) -> None:
            profiled = state["current_profiled"]
            if profiled is None:
                return
            patches_obj = self._patches_event_from_payload(event)
            # Store patch diffs by patch_id for later retrieval
            for patch in patches_obj.patches:
                state["patches_by_id"][patch.patch_id] = patch.diff
            audit = record_agent_call(
                "DA",
                lambda: self.da.audit(patches_obj, alarm),
                input_payload={"patches": event, "alarm": alarm.to_dict()},
            )
            state["final_audit"] = audit
            publish("DA", audit)

        def on_audit_done(event: dict[str, Any]) -> None:
            final_audit = state["final_audit"]
            if final_audit is None:
                return
            recorder.record(
                "attempt.finished",
                {
                    "retry_index": state["current_retry"],
                    "mode": state["current_profiled"].mode if state["current_profiled"] else "",
                    "audit": to_jsonable(final_audit),
                },
            )
            if final_audit.result == "passed" or state["current_retry"] >= self.max_retries:
                return
            state["last_failed_audit"] = final_audit
            state["current_retry"] += 1
            # Reset repository to original state before retry
            if original_commit_hash and hasattr(self.da, "config"):
                try:
                    import subprocess
                    repo_path = self.da.config.repo_path
                    recorder.record(
                        "repo.reset",
                        {
                            "retry_index": state["current_retry"],
                            "original_commit": original_commit_hash,
                            "reason": "Resetting repo to clean state before retry",
                        },
                    )
                    # Hard reset to original commit
                    subprocess.run(
                        ["git", "-C", str(repo_path), "reset", "--hard", original_commit_hash],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    # Clean untracked files
                    subprocess.run(
                        ["git", "-C", str(repo_path), "clean", "-fd"],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except Exception as exc:
                    recorder.record(
                        "repo.reset.failed",
                        {
                            "retry_index": state["current_retry"],
                            "error": {"type": type(exc).__name__, "message": str(exc)},
                        },
                    )
            start_retry(state["current_retry"])

        bus.subscribe("task.profiled", on_task_profiled)
        bus.subscribe("context.ready", on_context_ready)
        bus.subscribe("patches.ready", on_patches_ready)
        bus.subscribe("audit.done", on_audit_done)

        def start_retry(retry_index: int) -> None:
            previous_audit = state["last_failed_audit"]
            previous_task_append = state["last_task_append"]
            profiled = record_agent_call(
                "AO",
                lambda: self.ao.build_profiled_event(
                    task_id=task_id,
                    retry_index=retry_index,
                    alarm=alarm,
                    previous_audit=previous_audit,
                    previous_task_append=previous_task_append,
                ),
                input_payload={
                    "task_id": task_id,
                    "retry_index": retry_index,
                    "alarm": alarm.to_dict(),
                    "previous_audit": to_jsonable(previous_audit),
                    "previous_task_append": previous_task_append,
                },
            )
            # Save this round's task_append so the next retry round can accumulate it
            state["last_task_append"] = profiled.cp_input.prompt_injection.task_append
            state["current_profiled"] = profiled
            state["current_context"] = None
            mode_history.append(profiled.mode)
            for trace in getattr(self.ao, "last_llm_traces", []):
                payload = to_jsonable(trace)
                llm_traces.append(payload)
                recorder.record("llm.call", payload)
            recorder.record(
                "attempt.started",
                {
                    "retry_index": retry_index,
                    "mode": profiled.mode,
                    "profiled": profiled.to_dict(),
                },
            )
            publish("AO", profiled)

        start_retry(0)

        final_audit: AuditDoneEvent | None = state["final_audit"]
        if final_audit is None:
            raise RuntimeError("No audit result produced")
        final_status = "DONE" if final_audit.result == "passed" else "STOP_FAILED"

        # Extract model_patch for passing patch
        model_patch: dict[str, str] = {}
        if final_audit.result == "passed" and hasattr(final_audit, "patch_id"):
            passing_patch_id = final_audit.patch_id
            passing_diff = state["patches_by_id"].get(passing_patch_id, "")
            if passing_diff:
                model_patch = parse_diff_by_file(passing_diff)

        # Build model_patch output in standard evaluation format
        model_patch_output: dict[str, Any] = {}
        if model_patch:
            model_patch_output = {
                "instance_id": task_id,
                "model_patch": model_patch,
            }

        recorder.record(
            "task.finished",
            {
                "task_id": task_id,
                "final_status": final_status,
                "final_event": to_jsonable(final_audit),
                "model_patch_output": model_patch_output,
            },
        )
        return RunResult(
            task_id=task_id,
            final_status=final_status,
            mode_history=mode_history,
            final_event=final_audit.to_dict(),
            cp_debug_history=cp_debug_history,
            trace_entries=recorder.entries,
            llm_traces=llm_traces,
            model_patch_output=model_patch_output,
        )

    @staticmethod
    def _patches_event_from_payload(payload: dict[str, Any]):
        from oh_mas.core.schemas import PatchItem, PatchesReadyEvent

        return PatchesReadyEvent(
            task_id=payload["task_id"],
            mode=payload["mode"],
            patches=[PatchItem(**item) for item in payload.get("patches", [])],
        )
