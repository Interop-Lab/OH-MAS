from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field


@dataclass
class DependencyGraphResult:
    repo_name: str = ""
    commit_hash: str = ""
    kb_version: str = "unknown"
    degraded: bool = False
    degrade_reason: str = "none"
    latency_ms: int = 0
    graph_data: dict = field(default_factory=lambda: {
        "meta": {},
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
    })
    error: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KnowledgeResult:
    items: list[dict] = field(default_factory=list)
    total: int = 0
    latency_ms: int = 0
    kb_version: str = "unknown"
    degraded: bool = False
    degrade_reason: str = "none"
    error: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def ids(self) -> list[str]:
        ids: list[str] = []
        for item in self.items:
            text_id = item.get("text_id")
            if isinstance(text_id, str) and text_id:
                ids.append(text_id)
        return ids


class OHKBClient(ABC):
    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def query_framework_knowledge(
        self,
        *,
        rule_id: str = "",
        rule: str = "",
        language: str = "",
        max_items: int | None = None,
        request_id: str = "",
        task_id: str = "",
        retry_index: int | None = None,
        agent_name: str = "",
        timeout_ms: int | None = None,
        kb_version: str = "",
        fail_open: bool = True,
    ) -> KnowledgeResult:
        raise NotImplementedError

    @abstractmethod
    def query_rule_templates(
        self,
        *,
        rule_id: str = "",
        rule: str = "",
        language: str = "",
        max_items: int | None = None,
        request_id: str = "",
        task_id: str = "",
        retry_index: int | None = None,
        agent_name: str = "",
        timeout_ms: int | None = None,
        kb_version: str = "",
        fail_open: bool = True,
    ) -> KnowledgeResult:
        raise NotImplementedError

    @abstractmethod
    def query_repair_experience(
        self,
        *,
        rule_id: str = "",
        rule: str = "",
        language: str = "",
        max_items: int | None = None,
        request_id: str = "",
        task_id: str = "",
        retry_index: int | None = None,
        agent_name: str = "",
        timeout_ms: int | None = None,
        kb_version: str = "",
        fail_open: bool = True,
    ) -> KnowledgeResult:
        raise NotImplementedError
