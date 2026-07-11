#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Set, Tuple

from tree_sitter import Language, Node as TSNode, Parser
import tree_sitter_cpp as tscpp
import tree_sitter_typescript as tsts

ARKTS_EXT = {".ets", ".ts"}
CPP_EXT = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}

INCLUDE_RE = re.compile(r"^\s*#\s*include\s*([<\"])([^>\"]+)[>\"]", re.MULTILINE)
TS_LANG = Language(tsts.language_typescript())
CPP_LANG = Language(tscpp.language())


def run_git_head(repo_path: Path) -> str:
    try:
        p = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return p.stdout.strip()
    except Exception:
        return "unknown"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def node_id(kind: str, key: str) -> str:
    digest = hashlib.sha1(f"{kind}:{key}".encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}"


def scan_files(root: Path, exts: Set[str]) -> List[Path]:
    files: List[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        s = str(p).replace("\\", "/")
        if "/.git/" in s or "/node_modules/" in s or "/build/" in s or "/out/" in s:
            continue
        if p.suffix.lower() in exts:
            files.append(p)
    return files


def module_of(relpath: str) -> str:
    p = PurePosixPath(relpath)
    parent_parts = list(p.parent.parts)
    if not parent_parts:
        return "."
    if len(parent_parts) >= 2:
        return "/".join(parent_parts[:2])
    return parent_parts[0]


def text_of(node: TSNode, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def first_desc(node: TSNode, t: str) -> Optional[TSNode]:
    q = [node]
    while q:
        cur = q.pop(0)
        if cur.type == t:
            return cur
        q.extend(cur.children)
    return None


def all_desc(node: TSNode, t: str) -> List[TSNode]:
    out: List[TSNode] = []
    q = [node]
    while q:
        cur = q.pop(0)
        if cur.type == t:
            out.append(cur)
        q.extend(cur.children)
    return out


def load_ts_path_aliases(repo_root: Path) -> Dict[str, List[str]]:
    aliases: Dict[str, List[str]] = {}
    candidates = list(repo_root.rglob("tsconfig.json")) + list(repo_root.rglob("tsconfig.base.json"))
    for p in candidates[:20]:
        txt = read_text(p)
        if not txt:
            continue
        try:
            data = json.loads(txt)
        except Exception:
            continue
        compiler = data.get("compilerOptions", {})
        paths = compiler.get("paths", {})
        base_url = compiler.get("baseUrl", ".")
        base_dir = (p.parent / base_url).resolve()
        if not isinstance(paths, dict):
            continue
        for k, vals in paths.items():
            if not isinstance(vals, list):
                continue
            arr = []
            for v in vals:
                arr.append(str((base_dir / v).resolve()))
            aliases[k] = arr
    return aliases


def is_external_arkts_ref(ref: str) -> bool:
    return not ref.startswith(".")


def resolve_arkts_ref(ref: str, src_file: Path, repo_root: Path, aliases: Dict[str, List[str]], all_rel: Set[str]) -> Tuple[Optional[str], str]:
    candidates: List[Path] = []

    if ref.startswith("."):
        mode = "relative"
        base = (src_file.parent / ref).resolve()
        candidates.extend([base, Path(str(base) + ".ets"), Path(str(base) + ".ts"), base / "index.ets", base / "index.ts"])
    else:
        mode = "alias_or_package"
        for ak, targets in aliases.items():
            if "*" in ak:
                prefix = ak.split("*", 1)[0]
                if not ref.startswith(prefix):
                    continue
                suffix = ref[len(prefix) :]
                for t in targets:
                    t_prefix = t.split("*", 1)[0]
                    base = Path(t_prefix + suffix)
                    candidates.extend([base, Path(str(base) + ".ets"), Path(str(base) + ".ts"), base / "index.ets", base / "index.ts"])
            else:
                if ref != ak:
                    continue
                for t in targets:
                    base = Path(t)
                    candidates.extend([base, Path(str(base) + ".ets"), Path(str(base) + ".ts"), base / "index.ets", base / "index.ts"])
        base2 = (repo_root / ref).resolve()
        candidates.extend([base2, Path(str(base2) + ".ets"), Path(str(base2) + ".ts"), base2 / "index.ets", base2 / "index.ts"])

    for c in candidates:
        try:
            if c.is_file() and c.is_relative_to(repo_root):
                r = rel(c, repo_root)
                if r in all_rel:
                    return r, mode
        except Exception:
            continue
    return None, mode


def add_edge(edges: List[dict], seen: Set[Tuple[str, str, str]], src: str, dst: str, etype: str) -> None:
    k = (src, dst, etype)
    if k in seen:
        return
    seen.add(k)
    edges.append({"from": src, "to": dst, "type": etype})


def add_external_node(externals: Dict[str, str], nodes_external: List[dict], lang: str, name: str) -> str:
    key = f"{lang}:{name}"
    if key in externals:
        return externals[key]
    nid = node_id("external", key)
    externals[key] = nid
    nodes_external.append({"id": nid, "name": name})
    return nid


def parse_arkts_references(content: str) -> List[Tuple[str, str]]:
    src = content.encode("utf-8", errors="replace")
    parser = Parser(TS_LANG)
    tree = parser.parse(src)
    root = tree.root_node
    out: List[Tuple[str, str]] = []

    for ch in root.children:
        if ch.type not in {"import_statement", "export_statement"}:
            continue
        s = text_of(ch, src)
        str_node = first_desc(ch, "string")
        if str_node is None:
            if ch.type == "export_statement" and s.strip().startswith("export default"):
                out.append(("export_default_local", ""))
            continue

        raw = text_of(str_node, src).strip()
        ref = raw[1:-1] if len(raw) >= 2 and raw[0] in {'"', "'"} and raw[-1] == raw[0] else raw

        if ch.type == "import_statement":
            out.append(("import_side_effect" if " from " not in s else "import", ref))
        else:
            if "export * from" in s:
                out.append(("export_star", ref))
            elif " from " in s:
                out.append(("export_from", ref))
            elif s.strip().startswith("export default"):
                out.append(("export_default_local", ""))

    for ch in root.children:
        if ch.type == "class_declaration":
            txt = text_of(ch, src)
            if "extends" in txt:
                out.append(("inherit", ""))
            if "implements" in txt:
                out.append(("implement", ""))

    return out


def parse_cpp_symbols(content: str) -> Tuple[int, int, int]:
    src = content.encode("utf-8", errors="replace")
    parser = Parser(CPP_LANG)
    tree = parser.parse(src)
    root = tree.root_node
    ns = len(all_desc(root, "namespace_definition"))
    cls = len(all_desc(root, "class_specifier")) + len(all_desc(root, "struct_specifier"))
    enums = len(all_desc(root, "enum_specifier"))
    return ns, cls, enums


def resolve_cpp_include(include: str, src_file: Path, repo_root: Path, all_rel: Set[str]) -> Optional[str]:
    candidates: List[Path] = []
    if include.startswith("."):
        candidates.append((src_file.parent / include).resolve())
    else:
        candidates.append((src_file.parent / include).resolve())
        candidates.append((repo_root / include).resolve())

    for d in [repo_root, repo_root / "include", repo_root / "src", repo_root / "frameworks", repo_root / "services", repo_root / "interfaces", repo_root / "common", repo_root / "adapter", repo_root / "core"]:
        candidates.append((d / include).resolve())

    for c in candidates:
        try:
            if c.is_file() and c.is_relative_to(repo_root):
                r = rel(c, repo_root)
                if r in all_rel:
                    return r
        except Exception:
            continue
    return None


def parse_build_gn_deps(txt: str) -> List[str]:
    deps = []
    for m in re.finditer(r"(?:deps|public_deps|external_deps)\s*=\s*\[(.*?)\]", txt, flags=re.S):
        body = m.group(1)
        deps.extend(re.findall(r"['\"]([^'\"]+)['\"]", body))
    return deps


def add_module_edge(module_edges: List[dict], seen: Set[Tuple[str, str, str]], src: str, dst: str, etype: str) -> None:
    k = (src, dst, etype)
    if k in seen:
        return
    seen.add(k)
    module_edges.append({"from_module": src, "to_module": dst, "type": etype})


def split_edge_relations(edges: List[dict], file_ids: Set[str], external_ids: Set[str]) -> dict:
    file_to_file: List[dict] = []
    file_to_external: List[dict] = []
    for e in edges:
        src = e["from"]
        dst = e["to"]
        if src in file_ids and dst in file_ids:
            file_to_file.append(e)
        elif src in file_ids and dst in external_ids:
            file_to_external.append(e)
    return {
        "file_to_file": file_to_file,
        "file_to_external": file_to_external,
    }


def build_arkts_graph(repo_root: Path) -> dict:
    t0 = time.perf_counter()
    files = scan_files(repo_root, ARKTS_EXT)
    all_rel = {rel(f, repo_root) for f in files}
    aliases = load_ts_path_aliases(repo_root)

    nodes_files: List[dict] = []
    file_id: Dict[str, str] = {}
    for f in files:
        rp = rel(f, repo_root)
        nid = node_id("file", rp)
        nodes_files.append({"id": nid, "path": rp, "name": f.name})
        file_id[rp] = nid

    nodes_external: List[dict] = []
    ext_map: Dict[str, str] = {}

    edges: List[dict] = []
    seen: Set[Tuple[str, str, str]] = set()
    unresolved_local = 0
    external_edges = 0
    parse_failed = 0
    edge_types = Counter()
    resolve_modes = Counter()

    anchors = {
        "entry_components": [],
        "components": [],
        "builders": [],
        "inherit_files": [],
        "implement_files": [],
        "export_default_files": [],
    }

    for f in files:
        rp = rel(f, repo_root)
        src_id = file_id[rp]
        content = read_text(f)
        if not content:
            parse_failed += 1
            continue

        refs = parse_arkts_references(content)
        has_inherit = False
        has_implement = False
        has_export_default = False
        for etype, ref in refs:
            if etype == "inherit":
                has_inherit = True
                continue
            if etype == "implement":
                has_implement = True
                continue
            if etype == "export_default_local":
                has_export_default = True
                continue

            dst_rel, mode = resolve_arkts_ref(ref, f, repo_root, aliases, all_rel)
            resolve_modes[mode] += 1
            if dst_rel:
                add_edge(edges, seen, src_id, file_id[dst_rel], etype)
                edge_types[etype] += 1
                if etype == "export_star":
                    add_edge(edges, seen, src_id, file_id[dst_rel], "re_export")
                    edge_types["re_export"] += 1
                continue

            if is_external_arkts_ref(ref):
                ex_id = add_external_node(ext_map, nodes_external, "arkts", ref)
                ex_type = "import_external" if etype.startswith("import") else "export_external"
                add_edge(edges, seen, src_id, ex_id, ex_type)
                edge_types[ex_type] += 1
                external_edges += 1
            else:
                unresolved_local += 1

        if "@Entry" in content:
            anchors["entry_components"].append(rp)
        if "@Component" in content:
            anchors["components"].append(rp)
        if "@Builder" in content:
            anchors["builders"].append(rp)
        if has_inherit:
            anchors["inherit_files"].append(rp)
        if has_implement:
            anchors["implement_files"].append(rp)
        if has_export_default:
            anchors["export_default_files"].append(rp)

    for k in anchors:
        anchors[k] = sorted(set(anchors[k]))

    module_edges: List[dict] = []
    mseen: Set[Tuple[str, str, str]] = set()
    path_by_id = {x["id"]: x["path"] for x in nodes_files}
    for e in edges:
        if e["from"] not in path_by_id or e["to"] not in path_by_id:
            continue
        sm = module_of(path_by_id[e["from"]])
        dm = module_of(path_by_id[e["to"]])
        if sm != dm:
            add_module_edge(module_edges, mseen, sm, dm, "module_dep")

    file_ids = {x["id"] for x in nodes_files}
    external_ids = {x["id"] for x in nodes_external}
    relations = split_edge_relations(edges, file_ids, external_ids)

    build_ms = round((time.perf_counter() - t0) * 1000, 3)
    return {
        "meta": {
            "language": "arkts",
            "build_time_ms": build_ms,
            "files_scanned": len(files),
            "parse_failed_files": parse_failed,
            "file_node_count": len(nodes_files),
            "external_node_count": len(nodes_external),
            "edge_count": len(edges),
            "unresolved_local_edges": unresolved_local,
            "external_edges": external_edges,
            "edge_type_counts": dict(edge_types),
            "resolve_mode_counts": dict(resolve_modes),
            "module_dep_count": len(module_edges),
            "file_to_file_edge_count": len(relations["file_to_file"]),
            "file_to_external_edge_count": len(relations["file_to_external"]),
        },
        "graph": {
            "files": nodes_files,
            "external_modules": nodes_external,
            "relations": {
                **relations,
                "module_to_module": module_edges,
            },
            "anchors": anchors,
        },
    }


def build_cpp_graph(repo_root: Path) -> dict:
    t0 = time.perf_counter()
    files = scan_files(repo_root, CPP_EXT)
    all_rel = {rel(f, repo_root) for f in files}

    nodes_files: List[dict] = []
    file_id: Dict[str, str] = {}
    for f in files:
        rp = rel(f, repo_root)
        nid = node_id("file", rp)
        nodes_files.append({"id": nid, "path": rp, "name": f.name})
        file_id[rp] = nid

    nodes_external: List[dict] = []
    ext_map: Dict[str, str] = {}

    edges: List[dict] = []
    seen: Set[Tuple[str, str, str]] = set()
    unresolved_local = 0
    external_edges = 0
    parse_failed = 0
    edge_types = Counter()

    symbol_profiles: List[dict] = []

    for f in files:
        rp = rel(f, repo_root)
        src_id = file_id[rp]
        content = read_text(f)
        if not content:
            parse_failed += 1
            continue

        for m in INCLUDE_RE.finditer(content):
            delim, inc = m.group(1), m.group(2)
            dst_rel = resolve_cpp_include(inc, f, repo_root, all_rel)
            etype = "include_system" if delim == "<" else "include_local"

            if dst_rel:
                add_edge(edges, seen, src_id, file_id[dst_rel], etype)
                edge_types[etype] += 1
                if module_of(rp) == module_of(dst_rel):
                    add_edge(edges, seen, src_id, file_id[dst_rel], "same_module_include")
                    edge_types["same_module_include"] += 1
            else:
                if delim == "<":
                    ex_id = add_external_node(ext_map, nodes_external, "cpp", inc)
                    add_edge(edges, seen, src_id, ex_id, "include_system_external")
                    edge_types["include_system_external"] += 1
                    external_edges += 1
                else:
                    unresolved_local += 1

        ns, cls, _enums = parse_cpp_symbols(content)
        if ns or cls:
            symbol_profiles.append({"file": rp, "namespace_count": ns, "class_or_struct_count": cls})

    module_edges: List[dict] = []
    mseen: Set[Tuple[str, str, str]] = set()
    path_by_id = {x["id"]: x["path"] for x in nodes_files}
    for e in edges:
        if e["type"].startswith("include") and e["from"] in path_by_id and e["to"] in path_by_id:
            sm = module_of(path_by_id[e["from"]])
            dm = module_of(path_by_id[e["to"]])
            if sm != dm:
                add_module_edge(module_edges, mseen, sm, dm, "module_dep")

    gn_files = list(repo_root.rglob("BUILD.gn"))
    gn_dep_count = 0
    for gf in gn_files:
        txt = read_text(gf)
        if not txt:
            continue
        src_mod = module_of(rel(gf, repo_root))
        for dep in parse_build_gn_deps(txt):
            gn_dep_count += 1
            dep_mod = dep.split(":", 1)[0].lstrip("./") or src_mod
            add_module_edge(module_edges, mseen, src_mod, dep_mod, "gn_build_dep")

    file_ids = {x["id"] for x in nodes_files}
    external_ids = {x["id"] for x in nodes_external}
    relations = split_edge_relations(edges, file_ids, external_ids)

    build_ms = round((time.perf_counter() - t0) * 1000, 3)
    return {
        "meta": {
            "language": "cpp",
            "build_time_ms": build_ms,
            "files_scanned": len(files),
            "parse_failed_files": parse_failed,
            "file_node_count": len(nodes_files),
            "external_node_count": len(nodes_external),
            "edge_count": len(edges),
            "unresolved_local_edges": unresolved_local,
            "external_edges": external_edges,
            "edge_type_counts": dict(edge_types),
            "module_dep_count": len(module_edges),
            "build_gn_dep_refs": gn_dep_count,
            "file_to_file_edge_count": len(relations["file_to_file"]),
            "file_to_external_edge_count": len(relations["file_to_external"]),
        },
        "graph": {
            "files": nodes_files,
            "external_modules": nodes_external,
            "relations": {
                **relations,
                "module_to_module": module_edges,
            },
            "anchors": {"symbol_profiles": symbol_profiles},
        },
    }


def build_one(repo_root: Path, repo_name: str, language: str, out_root: Path) -> Path:
    commit = run_git_head(repo_root)
    graph = build_arkts_graph(repo_root) if language == "arkts" else build_cpp_graph(repo_root)
    graph["meta"].update({"repo_name": repo_name, "commit_hash": commit, "extractor_version": "v0.6"})

    # Use full 40-char commit hash for directory name to match dataset commit_hash exactly
    out_dir = out_root / f"{repo_name}@{commit}"
    out_dir.mkdir(parents=True, exist_ok=True)
    graph_path = out_dir / "graph_full.json"
    report_path = out_dir / "build_report.json"

    graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2))
    report = {
        "repo_name": repo_name,
        "repo_path": str(repo_root),
        "language": language,
        "commit_hash": commit,
        "output_graph": str(graph_path),
        **graph["meta"],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report_path


def parse_repo_arg(raw: str) -> Tuple[str, str]:
    if ":" not in raw:
        raise ValueError(f"Invalid --repo '{raw}', expected <repo_name>:<arkts|cpp>")
    name, lang = raw.split(":", 1)
    lang = lang.strip().lower()
    if lang not in {"arkts", "cpp"}:
        raise ValueError(f"Unsupported language '{lang}'")
    return name.strip(), lang


def main() -> None:
    ap = argparse.ArgumentParser(description="Build full dependency graphs for selected OH repos")
    ap.add_argument("--repositories-root", required=True, help="path to repositories root")
    ap.add_argument("--output-root", required=True, help="path to output root")
    ap.add_argument("--repo", action="append", required=True, help="repo spec in format <repo_name>:<arkts|cpp>, can repeat")
    args = ap.parse_args()

    repos_root = Path(args.repositories_root).resolve()
    out_root = Path(args.output_root).resolve()

    reports = []
    for spec in args.repo:
        repo_name, lang = parse_repo_arg(spec)
        repo_path = repos_root / repo_name
        if not repo_path.is_dir():
            raise FileNotFoundError(f"repo not found: {repo_path}")
        report_path = build_one(repo_path, repo_name, lang, out_root)
        reports.append(str(report_path))

    summary = {"generated_reports": reports, "count": len(reports)}
    (out_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
