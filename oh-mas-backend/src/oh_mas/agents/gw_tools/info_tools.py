from __future__ import annotations

from collections import deque

from oh_mas.agents.gw_tools.context import GWToolContext


def show_alarm(ctx: GWToolContext) -> dict:
    return ctx.record_step("show_alarm", ctx.alarm.to_dict())


def show_knowledge(ctx: GWToolContext) -> dict:
    pack = ctx.knowledge_pack
    output = {
        "rule_templates": list(pack.rule_templates) if pack else [],
        "experiences": list(pack.experiences) if pack else [],
    }
    return ctx.record_step("show_knowledge", output)


def show_rule_mode(ctx: GWToolContext) -> dict:
    output = {"rule_id": ctx.alarm.rule, **ctx.rule_mode}
    return ctx.record_step("show_rule_mode", output)


def show_graph_overview(ctx: GWToolContext) -> dict:
    graph = ctx.sliced_graph
    files = graph.get("files", [])
    relations = graph.get("relations", {})
    file_to_file = relations.get("file_to_file", [])
    file_to_external = relations.get("file_to_external", [])
    edge_types: dict[str, int] = {}
    for edge in file_to_file + file_to_external:
        edge_type = edge.get("type", "unknown")
        edge_types[edge_type] = edge_types.get(edge_type, 0) + 1

    output = {
        "alarm_file": ctx.alarm.file,
        "total_files": len(files),
        "total_edges": len(file_to_file) + len(file_to_external) + len(relations.get("module_to_module", [])),
        "edge_types": edge_types,
        "neighbors": _neighbors_by_hop(graph, ctx.alarm.file, max_hops=2),
    }
    return ctx.record_step("show_graph_overview", output)


def _neighbors_by_hop(graph: dict, alarm_file: str, *, max_hops: int) -> dict[str, list[str]]:
    files = graph.get("files", [])
    id_to_path = {node.get("id"): node.get("path", "") for node in files if node.get("id")}
    alarm_node_id = ""
    for node in files:
        path = node.get("path", "")
        if path == alarm_file or path.endswith(alarm_file) or alarm_file.endswith(path):
            alarm_node_id = node.get("id", "")
            break
    if not alarm_node_id:
        return {f"hop_{hop}": [] for hop in range(1, max_hops + 1)}

    adjacency: dict[str, list[str]] = {}
    for edge in graph.get("relations", {}).get("file_to_file", []):
        source = edge.get("from")
        target = edge.get("to")
        if not source or not target:
            continue
        adjacency.setdefault(source, []).append(target)
        adjacency.setdefault(target, []).append(source)

    result = {f"hop_{hop}": [] for hop in range(1, max_hops + 1)}
    visited = {alarm_node_id}
    queue = deque([(alarm_node_id, 0)])
    while queue:
        current, depth = queue.popleft()
        if depth >= max_hops:
            continue
        for neighbor in adjacency.get(current, []):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            hop = depth + 1
            path = id_to_path.get(neighbor, "")
            if path:
                result[f"hop_{hop}"].append(path)
            queue.append((neighbor, hop))
    return result
