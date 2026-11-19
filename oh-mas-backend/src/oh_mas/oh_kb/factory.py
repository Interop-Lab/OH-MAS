from __future__ import annotations

from pathlib import Path

from oh_mas.oh_kb.client import OHKBClient
from oh_mas.oh_kb.providers import (
    GraphExploreMockOHKBClient,
    LocalSeedOHKBClient,
    NullOHKBClient,
    RemoteOHKBClient,
)


def build_oh_kb_client(
    *,
    provider: str,
    seed_file: Path | None,
    fail_open: bool,
    graph_root: Path | None = None,
    linter_examples_root: Path | None = None,
    repair_experiences_path: Path | None = None,
    use_templates: bool = True,
    use_experiences: bool = True,
) -> OHKBClient:
    if provider == "null":
        return NullOHKBClient()
    if provider == "local_seed":
        return LocalSeedOHKBClient(seed_file=seed_file)
    if provider == "graph_explore_mock":
        if graph_root is None or (use_templates and linter_examples_root is None):
            raise ValueError("graph_explore_mock requires graph_root, and linter_examples_root when templates are enabled")
        return GraphExploreMockOHKBClient(
            graph_root=graph_root,
            linter_examples_root=linter_examples_root,
            repair_experiences_path=repair_experiences_path,
            use_templates=use_templates,
            use_experiences=use_experiences,
        )
    if provider == "remote":
        if fail_open:
            return NullOHKBClient()
        return RemoteOHKBClient()
    raise ValueError(f"Unknown OH-KB provider: {provider}")
