from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from oh_mas.oh_kb.client import DependencyGraphResult, KnowledgeResult, OHKBClient


class NullOHKBClient(OHKBClient):
    def query_dependency_graph(
        self,
        *,
        repo_name: str = "",
        commit_hash: str = "",
        alarm_file: str = "",
        mode: str = "",
        request_id: str = "",
        task_id: str = "",
        retry_index: int | None = None,
        agent_name: str = "",
        timeout_ms: int | None = None,
        kb_version: str = "",
        fail_open: bool = True,
    ) -> DependencyGraphResult:
        return DependencyGraphResult(
            degraded=True,
            degrade_reason="empty_data",
            graph_data={
                "meta": {
                    "language": "unknown",
                    "build_time_ms": 0,
                    "files_scanned": 0,
                    "parse_failed_files": 0,
                    "file_node_count": 0,
                    "external_node_count": 0,
                    "edge_count": 0,
                    "unresolved_local_edges": 0,
                    "external_edges": 0,
                    "edge_type_counts": {},
                    "module_dep_count": 0,
                    "file_to_file_edge_count": 0,
                    "file_to_external_edge_count": 0,
                    "extractor_version": "unknown",
                },
                "graph": {
                    "files": [],
                    "external_modules": [],
                    "relations": {
                        "file_to_file": [],
                        "file_to_external": [],
                        "module_to_module": [],
                    },
                    "anchors": {},
                },
            },
        )

    def query_framework_knowledge(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        return KnowledgeResult(items=[], total=0, degraded=True, degrade_reason="empty_data")

    def query_rule_templates(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        return KnowledgeResult(items=[], total=0, degraded=True, degrade_reason="empty_data")

    def query_repair_experience(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        return KnowledgeResult(items=[], total=0, degraded=True, degrade_reason="empty_data")


@dataclass
class SeedData:
    framework: dict[str, list[str]]
    rule_templates: dict[str, list[str]]
    experiences: dict[str, list[str]]


class LocalSeedOHKBClient(OHKBClient):
    def __init__(self, seed_file: Path):
        data = json.loads(seed_file.read_text()) if seed_file.exists() else {}
        self.seed = SeedData(
            framework=data.get("framework", {}),
            rule_templates=data.get("rule_templates", {}),
            experiences=data.get("experiences", {}),
        )

    def query_dependency_graph(
        self,
        *,
        repo_name: str = "",
        commit_hash: str = "",
        alarm_file: str = "",
        mode: str = "",
        request_id: str = "",
        task_id: str = "",
        retry_index: int | None = None,
        agent_name: str = "",
        timeout_ms: int | None = None,
        kb_version: str = "",
        fail_open: bool = True,
    ) -> DependencyGraphResult:
        file_id = "file:n1"
        graph_data = {
            "meta": {
                "language": "unknown",
                "build_time_ms": 0,
                "files_scanned": 1,
                "parse_failed_files": 0,
                "file_node_count": 1,
                "external_node_count": 0,
                "edge_count": 0,
                "unresolved_local_edges": 0,
                "external_edges": 0,
                "edge_type_counts": {},
                "module_dep_count": 0,
                "file_to_file_edge_count": 0,
                "file_to_external_edge_count": 0,
                "extractor_version": "local_seed_v1",
            },
            "graph": {
                "files": [{"id": file_id, "path": alarm_file, "name": alarm_file.split("/")[-1]}],
                "external_modules": [],
                "relations": {
                    "file_to_file": [],
                    "file_to_external": [],
                    "module_to_module": [],
                },
                "anchors": {
                    "alarm_files": [alarm_file],
                },
            },
        }
        return DependencyGraphResult(
            repo_name=repo_name or "local_seed_repo",
            commit_hash=commit_hash or "unknown",
            kb_version="local_seed_v1",
            degraded=False,
            degrade_reason="none",
            latency_ms=0,
            graph_data=graph_data,
            error=None,
        )

    @staticmethod
    def _to_text_items(layer: str, source: str, rule: str, texts: list[str]) -> list[dict]:
        items: list[dict] = []
        for idx, text in enumerate(texts):
            items.append(
                {
                    "text_id": f"{layer}:{rule}:{idx}",
                    "layer": layer,
                    "source": source,
                    "title": f"{layer}::{rule}::{idx}",
                    "text": text,
                }
            )
        return items

    def query_framework_knowledge(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        rid = rule_id or rule
        texts = self.seed.framework.get(rid, [])
        items = self._to_text_items("L2", "framework", rid, texts)
        if max_items is not None:
            items = items[:max_items]
        return KnowledgeResult(items=items, total=len(items), latency_ms=0, kb_version="local_seed_v1")

    def query_rule_templates(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        rid = rule_id or rule
        texts = self.seed.rule_templates.get(rid, [])
        items = self._to_text_items("L2", "rule_template", rid, texts)
        if max_items is not None:
            items = items[:max_items]
        return KnowledgeResult(items=items, total=len(items), latency_ms=0, kb_version="local_seed_v1")

    def query_repair_experience(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        rid = rule_id or rule
        texts = self.seed.experiences.get(rid, [])
        items = self._to_text_items("L3", "experience", rid, texts)
        if max_items is not None:
            items = items[:max_items]
        return KnowledgeResult(items=items, total=len(items), latency_ms=0, kb_version="local_seed_v1")


class GraphExploreMockOHKBClient(OHKBClient):
    def __init__(self, *, graph_root: Path, linter_examples_root: Path, repair_experiences_path: Path | None = None, max_graph_load: int = 256):
        self.graph_root = graph_root
        self.linter_examples_root = linter_examples_root
        self.max_graph_load = max_graph_load
        self._graphs = self._load_graphs()

        # Load repair experiences (L3)
        self.repair_experiences_path = repair_experiences_path
        self._repair_experiences = self._load_repair_experiences()

        self.kb_version = "graph_explore_mock_v2"

    def _load_graphs(self) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        if not self.graph_root.exists():
            return out
        candidates = sorted(self.graph_root.glob("*/graph_full.json"))[: self.max_graph_load]
        for p in candidates:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append((str(p), data))
        return out

    def _load_repair_experiences(self) -> dict:
        """Load repair experiences from JSON file (L3 knowledge)."""
        if not self.repair_experiences_path or not self.repair_experiences_path.exists():
            return {}
        try:
            data = json.loads(self.repair_experiences_path.read_text(encoding="utf-8"))
            return data.get("experiences", {})
        except Exception:
            return {}

    @staticmethod
    def _normalize_rule(rule: str) -> str:
        s = (rule or "").strip()
        if s.startswith("@"):
            return s
        if "/" in s:
            return s
        low = s.lower()
        cpp_guess = {"memleak", "nullpointer", "resourceleak", "useafterfree", "doublefree"}
        if low in cpp_guess:
            return f"cppcheck/{s}"
        return s

    @staticmethod
    def _safe_json_load(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _text_id(layer: str, rule: str, idx: int) -> str:
        return f"{layer}:{rule}:{idx}"

    @staticmethod
    def _short(text: str, n: int = 240) -> str:
        t = " ".join((text or "").split())
        if len(t) <= n:
            return t
        return t[: n - 3] + "..."

    def _build_text_items(self, *, layer: str, source: str, rule: str, from_files: list[Path], fallback_title: str) -> list[dict]:
        items: list[dict] = []
        idx = 0
        for fp in from_files:
            data = self._safe_json_load(fp)
            if not data:
                continue
            rid = str(data.get("rule_id", rule))
            explanation = str(data.get("explanation", "")).strip()
            examples = data.get("examples", [])
            if explanation:
                items.append(
                    {
                        "text_id": self._text_id(layer, rid, idx),
                        "layer": layer,
                        "source": source,
                        "title": f"{fallback_title} explanation",
                        "text": explanation,
                    }
                )
                idx += 1
            if isinstance(examples, list) and examples:
                ex = examples[0] or {}
                buggy = self._short(str(ex.get("buggy_code", "")))
                fixed = self._short(str(ex.get("fixed_code", "")))
                if buggy:
                    items.append(
                        {
                            "text_id": self._text_id(layer, rid, idx),
                            "layer": layer,
                            "source": source,
                            "title": f"{fallback_title} buggy snippet",
                            "text": buggy,
                        }
                    )
                    idx += 1
                if fixed:
                    items.append(
                        {
                            "text_id": self._text_id(layer, rid, idx),
                            "layer": layer,
                            "source": source,
                            "title": f"{fallback_title} fixed snippet",
                            "text": fixed,
                        }
                    )
                    idx += 1
        return items

    def _find_example_files(self, norm_rule: str) -> list[Path]:
        all_json = list(self.linter_examples_root.rglob("*.json"))
        if not all_json:
            return []
        hits: list[Path] = []
        target = norm_rule.strip().lower()
        tail = target.split("/", 1)[-1]
        for p in all_json:
            data = self._safe_json_load(p)
            rid = str(data.get("rule_id", "")).strip().lower()
            if rid == target:
                hits.append(p)
                continue
            if rid.endswith("/" + tail) and target.endswith(tail):
                hits.append(p)
                continue
            if p.stem.lower() == tail.lower():
                hits.append(p)
        return hits[:6]

    def query_dependency_graph(
        self,
        *,
        repo_name: str = "",
        commit_hash: str = "",
        alarm_file: str = "",
        mode: str = "",
        request_id: str = "",
        task_id: str = "",
        retry_index: int | None = None,
        agent_name: str = "",
        timeout_ms: int | None = None,
        kb_version: str = "",
        fail_open: bool = True,
    ) -> DependencyGraphResult:
        t0 = time.perf_counter()
        af = alarm_file.replace("\\", "/")
        chosen: dict | None = None
        chosen_repo_name = ""
        chosen_commit = ""
        requested_repo = repo_name.strip()
        requested_commit = commit_hash.strip()
        for _, g in self._graphs:
            meta = g.get("meta") or {}
            graph_repo = str(meta.get("repo_name", ""))
            graph_commit = str(meta.get("commit_hash", ""))
            if requested_repo and graph_repo != requested_repo:
                continue
            # Simple exact match: graph directories now use full 40-char commit hash
            if requested_commit and graph_commit != requested_commit:
                continue
            files = (((g.get("graph") or {}).get("files")) or [])
            for it in files:
                p = str((it or {}).get("path", "")).replace("\\", "/")
                if p and (p == af or p.endswith(af) or af.endswith(p)):
                    chosen = g
                    chosen_repo_name = graph_repo
                    chosen_commit = graph_commit
                    break
            if chosen is not None:
                break

        if chosen is None and self._graphs and not requested_repo and not requested_commit:
            is_arkts = af.endswith(".ets") or af.endswith(".ts")
            for _, g in self._graphs:
                lang = str((g.get("meta") or {}).get("language", ""))
                if (is_arkts and lang == "arkts") or ((not is_arkts) and lang == "cpp"):
                    chosen = g
                    meta = g.get("meta") or {}
                    chosen_repo_name = str(meta.get("repo_name", ""))
                    chosen_commit = str(meta.get("commit_hash", ""))
                    break
            if chosen is None:
                chosen = self._graphs[0][1]
                meta = chosen.get("meta") or {}
                chosen_repo_name = str(meta.get("repo_name", ""))
                chosen_commit = str(meta.get("commit_hash", ""))

        latency = int((time.perf_counter() - t0) * 1000)
        if chosen is None:
            return DependencyGraphResult(
                repo_name="",
                commit_hash="",
                kb_version=self.kb_version,
                degraded=True,
                degrade_reason="empty_data",
                latency_ms=latency,
                graph_data={
                    "meta": {
                        "language": "unknown",
                        "build_time_ms": 0,
                        "files_scanned": 0,
                        "parse_failed_files": 0,
                        "file_node_count": 0,
                        "external_node_count": 0,
                        "edge_count": 0,
                        "unresolved_local_edges": 0,
                        "external_edges": 0,
                        "edge_type_counts": {},
                        "module_dep_count": 0,
                        "file_to_file_edge_count": 0,
                        "file_to_external_edge_count": 0,
                        "extractor_version": "unknown",
                    },
                    "graph": {
                        "files": [],
                        "external_modules": [],
                        "relations": {
                            "file_to_file": [],
                            "file_to_external": [],
                            "module_to_module": [],
                        },
                        "anchors": {},
                    },
                },
                error=None,
            )

        return DependencyGraphResult(
            repo_name=chosen_repo_name,
            commit_hash=chosen_commit,
            kb_version=self.kb_version,
            degraded=False,
            degrade_reason="none",
            latency_ms=latency,
            graph_data={"meta": chosen.get("meta", {}), "graph": chosen.get("graph", {})},
            error=None,
        )

    def _query_text_items(
        self,
        *,
        layer: str,
        source: str,
        fallback_title: str,
        rule: str,
        max_items: int | None = None,
    ) -> KnowledgeResult:
        t0 = time.perf_counter()
        norm = self._normalize_rule(rule)
        files = self._find_example_files(norm)
        items = self._build_text_items(
            layer=layer,
            source=source,
            rule=norm,
            from_files=files,
            fallback_title=fallback_title,
        )
        if max_items is not None:
            items = items[:max_items]
        latency = int((time.perf_counter() - t0) * 1000)
        degraded = len(items) == 0
        return KnowledgeResult(
            items=items,
            total=len(items),
            latency_ms=latency,
            kb_version=self.kb_version,
            degraded=degraded,
            degrade_reason="none" if not degraded else "empty_data",
            error=None,
        )

    def query_framework_knowledge(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        return self._query_text_items(layer="L2", source="framework", fallback_title="framework", rule=rule_id or rule, max_items=max_items)

    def query_rule_templates(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        return self._query_text_items(layer="L2", source="rule_template", fallback_title="rule template", rule=rule_id or rule, max_items=max_items)

    def query_repair_experience(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        """
        Query repair experience (L3 knowledge).

        Returns curated repair experiences from repair_experiences.json.
        If no curated experience exists, returns empty result (no fallback to linter examples).
        """
        t0 = time.perf_counter()
        norm_rule = self._normalize_rule(rule_id or rule)

        items: list[dict] = []
        idx = 0

        # Try curated repair experiences
        if norm_rule in self._repair_experiences:
            experiences = self._repair_experiences[norm_rule]
            for exp in experiences:
                if not isinstance(exp, dict):
                    continue

                exp_id = exp.get("experience_id", f"exp:{norm_rule}:{idx}")
                title = exp.get("title", "Repair experience")
                category = exp.get("category", "general")
                priority = exp.get("priority", "normal")
                content = exp.get("content", "")

                if content:
                    items.append({
                        "text_id": exp_id,
                        "layer": "L3",
                        "source": "curated_experience",
                        "title": f"[{priority.upper()}] {title}",
                        "text": content,
                        "category": category,
                        "priority": priority,
                    })
                    idx += 1

        # Apply max_items limit
        if max_items is not None:
            items = items[:max_items]

        latency = int((time.perf_counter() - t0) * 1000)
        degraded = len(items) == 0

        return KnowledgeResult(
            items=items,
            total=len(items),
            latency_ms=latency,
            kb_version=self.kb_version,
            degraded=degraded,
            degrade_reason="no_curated_experience" if degraded else "none",
            error=None,
        )


class RemoteOHKBClient(OHKBClient):
    def query_dependency_graph(
        self,
        *,
        repo_name: str = "",
        commit_hash: str = "",
        alarm_file: str = "",
        mode: str = "",
        request_id: str = "",
        task_id: str = "",
        retry_index: int | None = None,
        agent_name: str = "",
        timeout_ms: int | None = None,
        kb_version: str = "",
        fail_open: bool = True,
    ) -> DependencyGraphResult:
        raise RuntimeError("RemoteOHKBClient not implemented in V1")

    def query_framework_knowledge(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        raise RuntimeError("RemoteOHKBClient not implemented in V1")

    def query_rule_templates(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        raise RuntimeError("RemoteOHKBClient not implemented in V1")

    def query_repair_experience(self, *, rule_id: str = "", rule: str = "", language: str = "", max_items: int | None = None, request_id: str = "", task_id: str = "", retry_index: int | None = None, agent_name: str = "", timeout_ms: int | None = None, kb_version: str = "", fail_open: bool = True) -> KnowledgeResult:
        raise RuntimeError("RemoteOHKBClient not implemented in V1")
