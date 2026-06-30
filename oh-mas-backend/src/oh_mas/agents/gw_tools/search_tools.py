from __future__ import annotations

from collections import deque
from fnmatch import fnmatch
import re

from oh_mas.agents.gw_tools.context import GWToolContext


def list_neighbors(
    ctx: GWToolContext,
    *,
    max_hops: int = 2,
    file_pattern: str | None = None,
    include_external: bool = False,
) -> dict:
    graph = ctx.sliced_graph
    files = graph.get("files", [])
    id_to_path = {node.get("id"): node.get("path", "") for node in files if node.get("id")}
    alarm_node_id = _find_alarm_node_id(files, ctx.alarm.file)
    neighbors: dict[str, list[dict]] = {f"hop_{hop}": [] for hop in range(1, max_hops + 1)}
    if alarm_node_id:
        adjacency = _build_adjacency(graph)
        visited = {alarm_node_id}
        queue = deque([(alarm_node_id, 0)])
        while queue:
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for neighbor, relation in adjacency.get(current, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                hop = depth + 1
                path = id_to_path.get(neighbor, "")
                if path and _matches(path, file_pattern):
                    neighbors[f"hop_{hop}"].append({"file": path, "relation": relation})
                queue.append((neighbor, hop))

    external: list[dict] = []
    if include_external:
        external_by_id = {
            module.get("id"): module
            for module in graph.get("external_modules", [])
            if module.get("id")
        }
        for edge in graph.get("relations", {}).get("file_to_external", []):
            module = external_by_id.get(edge.get("to"), {})
            external.append(
                {
                    "source": id_to_path.get(edge.get("from"), edge.get("from", "")),
                    "module": module.get("name") or edge.get("to", ""),
                    "relation": edge.get("type", "external"),
                }
            )

    output = {
        "alarm_file": ctx.alarm.file,
        "neighbors": neighbors,
        "external": external,
        "total_count": sum(len(items) for items in neighbors.values()) + len(external),
    }
    return ctx.record_step(
        "list_neighbors",
        output,
        max_hops=max_hops,
        file_pattern=file_pattern,
        include_external=include_external,
    )


def grep_neighbors(
    ctx: GWToolContext,
    *,
    pattern: str,
    max_hops: int = 2,
    file_pattern: str | None = None,
    max_files: int = 10,
    max_matches_per_file: int = 3,
) -> dict:
    graph = ctx.sliced_graph
    files = graph.get("files", [])
    id_to_path = {node.get("id"): node.get("path", "") for node in files if node.get("id")}
    alarm_node_id = _find_alarm_node_id(files, ctx.alarm.file)
    regex = re.compile(pattern)
    results: list[dict] = []
    files_searched = 0
    if alarm_node_id and ctx.repo_path is not None:
        adjacency = _build_adjacency(graph)
        visited = {alarm_node_id}
        queue = deque([(alarm_node_id, 0)])
        while queue and files_searched < max_files:
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for neighbor, _relation in adjacency.get(current, []):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                hop = depth + 1
                path = id_to_path.get(neighbor, "")
                if path and _matches(path, file_pattern):
                    matches = _grep_repo_file(ctx, path, regex, max_matches=max_matches_per_file)
                    files_searched += 1
                    if matches:
                        results.append({"file": path, "hop_distance": hop, "matches": matches})
                    if files_searched >= max_files:
                        break
                queue.append((neighbor, hop))

    output = {
        "pattern": pattern,
        "files_searched": files_searched,
        "files_matched": len(results),
        "results": results,
        "truncated": files_searched >= max_files,
    }
    return ctx.record_step(
        "grep_neighbors",
        output,
        pattern=pattern,
        max_hops=max_hops,
        file_pattern=file_pattern,
        max_files=max_files,
        max_matches_per_file=max_matches_per_file,
    )


def _find_alarm_node_id(files: list[dict], alarm_file: str) -> str:
    for node in files:
        path = node.get("path", "")
        if path == alarm_file or path.endswith(alarm_file) or alarm_file.endswith(path):
            return node.get("id", "")
    return ""


def _build_adjacency(graph: dict) -> dict[str, list[tuple[str, str]]]:
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in graph.get("relations", {}).get("file_to_file", []):
        source = edge.get("from")
        target = edge.get("to")
        if not source or not target:
            continue
        relation = edge.get("type", "file_to_file")
        adjacency.setdefault(source, []).append((target, relation))
        adjacency.setdefault(target, []).append((source, relation))
    return adjacency


def _matches(path: str, file_pattern: str | None) -> bool:
    return not file_pattern or fnmatch(path, file_pattern) or fnmatch(path.rsplit("/", 1)[-1], file_pattern)


def _grep_repo_file(ctx: GWToolContext, path: str, regex: re.Pattern, *, max_matches: int) -> list[dict]:
    root = ctx.repo_path
    if root is None:
        return []
    file_path = (root / path).resolve()
    try:
        if not file_path.is_relative_to(root) or not file_path.is_file():
            return []
    except ValueError:
        return []
    matches: list[dict] = []
    for line_no, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        match = regex.search(line)
        if not match:
            continue
        matches.append({"line": line_no, "content": line, "match": match.group(0)})
        if len(matches) >= max_matches:
            break
    return matches
