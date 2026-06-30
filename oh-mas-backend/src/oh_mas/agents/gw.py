from __future__ import annotations

from collections import deque
import json
from pathlib import Path

from oh_mas.core.schemas import (
    ContextReadyEvent,
    GraphSlice,
    GWProfileInput,
    KnowledgePack,
    TaskProfiledEvent,
)
from oh_mas.agents.gw_worker import GWWorker, GWWorkerConfig
from oh_mas.oh_kb.client import OHKBClient


class GWAgent:
    def __init__(
        self,
        kb_client: OHKBClient,
        worker: GWWorker | None = None,
        repo_root: str = "",
        worker_config: GWWorkerConfig | None = None,
    ):
        self.kb_client = kb_client
        self.worker = worker or GWWorker(worker_config)
        self.repo_root = repo_root
        self.last_trace_steps: list[dict] = []
        self.last_llm_traces: list[dict] = []
        self.last_debug: dict = {}

    def build_context(
        self,
        gw_input: GWProfileInput,
        knowledge_pack: KnowledgePack | None = None,
        trajectory_path: str | None = None,
    ) -> ContextReadyEvent:
        """Build context from projected GW input. Does not receive full TaskProfiledEvent."""
        self.last_trace_steps = []
        self.last_llm_traces = []
        self.last_debug = {}
        dep = self.kb_client.query_dependency_graph(
            alarm_file=gw_input.alarm.file,
            mode=gw_input.mode,
            repo_name=gw_input.alarm.project,
            commit_hash=gw_input.alarm.commit_hash,
        )
        full_graph = dep.graph_data.get("graph", {})

        # Always execute graph slicing (mode determines depth)
        sliced_graph = self._slice_graph(
            full_graph=full_graph,
            alarm_file=gw_input.alarm.file,
            mode=gw_input.mode,
        )

        # Build final graph data with updated metadata
        original_meta = dep.graph_data.get("meta", {})
        sliced_meta = self._update_slice_meta(original_meta, sliced_graph)
        final_graph_data = {
            "meta": sliced_meta,
            "graph": sliced_graph,
        }
        self.last_debug["bfs_slice"] = {
            "files_count": len(sliced_graph.get("files", [])),
            "edges_count": len(sliced_graph.get("relations", {}).get("file_to_file", []))
            + len(sliced_graph.get("relations", {}).get("file_to_external", [])),
            "slice_mode": sliced_graph.get("anchors", {}).get("slice_mode", gw_input.mode),
        }

        precise_slice = None
        slicing_mode = "legacy"
        if gw_input.gw_input.build_semantic_graph or gw_input.mode in {"medium", "hard"}:
            worker_result = self.worker.run(
                gw_input=gw_input,
                graph_data=final_graph_data,
                knowledge_pack=knowledge_pack,
                repo_root=self.repo_root,
                retry_index=gw_input.retry_index,
                introduced_rules=gw_input.introduced_rules,
            )
            precise = worker_result.precise_slice
            self.last_trace_steps = list(worker_result.trace_steps)
            self.last_llm_traces = list(worker_result.llm_traces)
            precise_slice = precise.to_dict()
            slicing_mode = "agent_driven"
            self.last_debug["agent"] = {
                "mode": worker_result.mode,
                "must_fix": len(precise.repair_contract.must_fix),
                "must_not_touch": len(precise.repair_contract.must_not_touch),
                "allowed_transformations": len(precise.repair_contract.allowed_transformations),
            }
            if trajectory_path:
                self._write_trajectory(trajectory_path=trajectory_path, gw_input=gw_input, context=precise_slice)

        return ContextReadyEvent(
            task_id=gw_input.task_id,
            mode=gw_input.mode,
            graph_slice=GraphSlice(graph_data=final_graph_data),
            precise_slice=precise_slice,
            slicing_mode=slicing_mode,
            gw_trajectory_path=trajectory_path,
        )

    def on_task_profiled(self, profiled: TaskProfiledEvent) -> ContextReadyEvent:
        introduced_rules: list[str] = []
        if profiled.previous_audit_feedback:
            seen: set[str] = set()
            for diag in profiled.previous_audit_feedback.patch_diagnostics:
                for warning in diag.introduced_warnings:
                    if warning.rule and warning.rule not in seen:
                        introduced_rules.append(warning.rule)
                        seen.add(warning.rule)

        return self.build_context(
            GWProfileInput(
                task_id=profiled.task_id,
                mode=profiled.mode,
                alarm=profiled.alarm,
                gw_input=profiled.gw_input,
                retry_index=profiled.retry_index,
                introduced_rules=introduced_rules,
            ),
            knowledge_pack=profiled.cp_input.knowledge_pack,
        )

    def _write_trajectory(self, *, trajectory_path: str, gw_input: GWProfileInput, context: dict) -> None:
        path = Path(trajectory_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "agent": "GW",
            "input": gw_input.to_dict(),
            "steps": self.last_trace_steps,
            "llm_traces": self.last_llm_traces,
            "output": context,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _slice_graph(
        self,
        full_graph: dict,
        alarm_file: str,
        mode: str,
    ) -> dict:
        """
        Execute BFS-based graph slicing to extract alarm-centric subgraph.

        Args:
            full_graph: Full dependency graph from OH-KB
            alarm_file: Path to the file containing the alarm
            mode: Execution mode (easy/medium/hard)

        Returns:
            Sliced subgraph containing relevant files and relations
        """
        # Determine hop distance based on mode
        max_hops = 1 if mode == "easy" else 2

        # Find alarm file node
        files = full_graph.get("files", [])
        alarm_node_id = None
        for file_node in files:
            file_path = file_node.get("path", "")
            # Match alarm file (support both exact match and suffix match)
            if file_path == alarm_file or file_path.endswith(alarm_file) or alarm_file.endswith(file_path):
                alarm_node_id = file_node.get("id")
                break

        if not alarm_node_id:
            # Fallback: return minimal slice if alarm file not found
            return self._minimal_slice(full_graph, alarm_file)

        # BFS to collect related nodes
        visited_nodes = {alarm_node_id}
        queue = deque([(alarm_node_id, 0)])

        file_to_file_edges = full_graph.get("relations", {}).get("file_to_file", [])

        while queue:
            current_node, depth = queue.popleft()

            if depth >= max_hops:
                continue

            # Explore both outgoing and incoming edges
            for edge in file_to_file_edges:
                from_node = edge.get("from")
                to_node = edge.get("to")

                if from_node == current_node and to_node not in visited_nodes:
                    visited_nodes.add(to_node)
                    queue.append((to_node, depth + 1))
                elif to_node == current_node and from_node not in visited_nodes:
                    visited_nodes.add(from_node)
                    queue.append((from_node, depth + 1))

        # Build sliced graph
        sliced_files = [f for f in files if f.get("id") in visited_nodes]
        sliced_edges = [
            e for e in file_to_file_edges
            if e.get("from") in visited_nodes and e.get("to") in visited_nodes
        ]

        # Also collect external module edges connected to sliced files
        file_to_external_edges = full_graph.get("relations", {}).get("file_to_external", [])
        sliced_external_edges = [
            e for e in file_to_external_edges
            if e.get("from") in visited_nodes
        ]

        # Collect external modules referenced by sliced files
        external_module_ids = {e.get("to") for e in sliced_external_edges if e.get("to")}
        external_modules = full_graph.get("external_modules", [])
        sliced_external_modules = [
            m for m in external_modules
            if m.get("id") in external_module_ids
        ]

        return {
            "files": sliced_files,
            "external_modules": sliced_external_modules,
            "relations": {
                "file_to_file": sliced_edges,
                "file_to_external": sliced_external_edges,
                "module_to_module": [],  # Not sliced for now
            },
            "anchors": {
                "alarm_file": alarm_file,
                "alarm_node_id": alarm_node_id,
                "slice_mode": mode,
                "max_hops": max_hops,
            },
        }

    def _minimal_slice(self, full_graph: dict, alarm_file: str) -> dict:
        """
        Fallback: create minimal slice containing only the alarm file.

        Args:
            full_graph: Full dependency graph
            alarm_file: Path to the alarm file

        Returns:
            Minimal graph with single file node
        """
        files = full_graph.get("files", [])
        alarm_node = None

        for file_node in files:
            file_path = file_node.get("path", "")
            if file_path == alarm_file or file_path.endswith(alarm_file):
                alarm_node = file_node
                break

        if alarm_node:
            return {
                "files": [alarm_node],
                "external_modules": [],
                "relations": {
                    "file_to_file": [],
                    "file_to_external": [],
                    "module_to_module": [],
                },
                "anchors": {
                    "alarm_file": alarm_file,
                    "alarm_node_id": alarm_node.get("id"),
                    "slice_mode": "minimal",
                },
            }
        else:
            # Ultimate fallback: synthetic node
            return {
                "files": [
                    {
                        "id": "file:synthetic",
                        "path": alarm_file,
                        "name": alarm_file.split("/")[-1],
                    }
                ],
                "external_modules": [],
                "relations": {
                    "file_to_file": [],
                    "file_to_external": [],
                    "module_to_module": [],
                },
                "anchors": {
                    "alarm_file": alarm_file,
                    "alarm_node_id": "file:synthetic",
                    "slice_mode": "synthetic",
                },
            }


    @staticmethod
    def _update_slice_meta(original_meta: dict, sliced_graph: dict) -> dict:
        """
        Update graph metadata to reflect sliced graph statistics.

        Used for system observability and debugging, not sent to CP Worker.
        """
        sliced_files = sliced_graph.get("files", [])
        sliced_external_modules = sliced_graph.get("external_modules", [])
        relations = sliced_graph.get("relations", {})
        file_to_file_edges = relations.get("file_to_file", [])
        file_to_external_edges = relations.get("file_to_external", [])
        module_to_module_edges = relations.get("module_to_module", [])

        # Count edge types
        edge_type_counts: dict[str, int] = {}
        for edge in file_to_file_edges:
            edge_type = edge.get("type", "unknown")
            edge_type_counts[edge_type] = edge_type_counts.get(edge_type, 0) + 1
        for edge in file_to_external_edges:
            edge_type = edge.get("type", "unknown")
            edge_type_counts[edge_type] = edge_type_counts.get(edge_type, 0) + 1

        total_edges = len(file_to_file_edges) + len(file_to_external_edges) + len(module_to_module_edges)

        # Create updated metadata
        updated_meta = dict(original_meta)
        updated_meta.update({
            "file_node_count": len(sliced_files),
            "external_node_count": len(sliced_external_modules),
            "edge_count": total_edges,
            "file_to_file_edge_count": len(file_to_file_edges),
            "file_to_external_edge_count": len(file_to_external_edges),
            "edge_type_counts": edge_type_counts,
            "sliced": True,
        })

        return updated_meta
