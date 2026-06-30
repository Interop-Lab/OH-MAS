from __future__ import annotations

import re
from pathlib import Path

from oh_mas.agents.gw_tools.context import GWToolContext


def read_lines(
    ctx: GWToolContext,
    *,
    file: str,
    start_line: int,
    end_line: int,
    context_before: int = 0,
    context_after: int = 0,
) -> dict:
    content = _read_repo_file(ctx, file)
    if content is None:
        output = _missing_repo_output(file)
        return ctx.record_step("read_lines", output, file=file, start_line=start_line, end_line=end_line)

    lines = content.splitlines()
    actual_start = max(1, start_line - max(0, context_before))
    actual_end = min(len(lines), end_line + max(0, context_after))
    selected = lines[actual_start - 1:actual_end]
    formatted = "\n".join(f"{idx:>5}│{line}" for idx, line in enumerate(selected, start=actual_start))
    output = {
        "file": file,
        "requested_range": {"start": start_line, "end": end_line},
        "actual_range": {"start": actual_start, "end": actual_end},
        "content": formatted,
        "line_count": len(selected),
        "truncated": False,
    }
    return ctx.record_step(
        "read_lines",
        output,
        file=file,
        start_line=start_line,
        end_line=end_line,
        context_before=context_before,
        context_after=context_after,
    )


def grep_file(ctx: GWToolContext, *, file: str, pattern: str, max_matches: int = 20, context_lines: int = 0) -> dict:
    content = _read_repo_file(ctx, file)
    if content is None:
        output = _missing_repo_output(file)
        output.update({"pattern": pattern, "matches": [], "total_matches": 0, "truncated": False})
        return ctx.record_step("grep_file", output, file=file, pattern=pattern)

    regex = re.compile(pattern)
    lines = content.splitlines()
    matches: list[dict] = []
    total_matches = 0
    for idx, line in enumerate(lines, start=1):
        match = regex.search(line)
        if not match:
            continue
        total_matches += 1
        if len(matches) >= max_matches:
            continue
        before_start = max(1, idx - context_lines)
        after_end = min(len(lines), idx + context_lines)
        matches.append(
            {
                "line": idx,
                "column": match.start() + 1,
                "match": match.group(0),
                "line_content": line,
                "context_before": lines[before_start - 1:idx - 1],
                "context_after": lines[idx:after_end],
            }
        )
    output = {
        "file": file,
        "pattern": pattern,
        "matches": matches,
        "total_matches": total_matches,
        "truncated": total_matches > len(matches),
    }
    return ctx.record_step(
        "grep_file",
        output,
        file=file,
        pattern=pattern,
        max_matches=max_matches,
        context_lines=context_lines,
    )


def view_file_structure(ctx: GWToolContext, *, file: str, mode: str = "outline", focus_symbols: list[str] | None = None) -> dict:
    content = _read_repo_file(ctx, file)
    if content is None:
        output = _missing_repo_output(file)
        output.update({"outline": [], "symbols": [], "parse_method": "unavailable"})
        return ctx.record_step("view_file_structure", output, file=file, mode=mode, focus_symbols=focus_symbols)

    lines = content.splitlines()
    symbols = _regex_symbols(lines)
    if focus_symbols:
        focus = set(focus_symbols)
        symbols = [symbol for symbol in symbols if symbol.get("name") in focus]
    output = {
        "file": file,
        "total_lines": len(lines),
        "language": _language_for(file),
        "parse_method": "regex",
    }
    if mode == "detailed":
        output["symbols"] = symbols
    else:
        output["outline"] = [
            {k: v for k, v in symbol.items() if k in {"type", "name", "line", "end_line", "preview"}}
            for symbol in symbols
        ]
    return ctx.record_step("view_file_structure", output, file=file, mode=mode, focus_symbols=focus_symbols)


def read_symbol(
    ctx: GWToolContext,
    *,
    file: str,
    symbol: str,
    symbol_type: str = "",
    include_decorators: bool = True,
    include_class_header: bool = True,
    max_lines: int = 120,
) -> dict:
    content = _read_repo_file(ctx, file)
    if content is None:
        output = _missing_repo_output(file)
        output.update({"symbol": symbol, "content": "", "truncated": False})
        return ctx.record_step("read_symbol", output, file=file, symbol=symbol, symbol_type=symbol_type)

    lines = content.splitlines()
    symbols = _regex_symbols(lines)
    candidates = [
        item for item in symbols
        if item.get("name") == symbol and (not symbol_type or item.get("type") == symbol_type)
    ]
    if not candidates:
        output = {"file": file, "symbol": symbol, "symbol_type": symbol_type, "range": None, "content": "", "truncated": False}
        return ctx.record_step("read_symbol", output, file=file, symbol=symbol, symbol_type=symbol_type)

    item = candidates[0]
    start = int(item["line"])
    end = int(item.get("end_line") or start)
    if include_decorators:
        start = _include_decorator_start(lines, start)
    truncated = end - start + 1 > max_lines
    actual_end = min(end, start + max_lines - 1)
    selected = "\n".join(lines[start - 1:actual_end])
    output = {
        "file": file,
        "symbol": symbol,
        "symbol_type": item.get("type", symbol_type),
        "range": {"start": start, "end": actual_end},
        "content": selected,
        "context": {},
        "truncated": truncated,
        "truncated_at_line": actual_end if truncated else None,
    }
    return ctx.record_step(
        "read_symbol",
        output,
        file=file,
        symbol=symbol,
        symbol_type=symbol_type,
        include_decorators=include_decorators,
        include_class_header=include_class_header,
        max_lines=max_lines,
    )


def _read_repo_file(ctx: GWToolContext, file: str) -> str | None:
    root = ctx.repo_path
    if root is None:
        return None
    path = (root / file).resolve()
    try:
        if not path.is_relative_to(root) or not path.is_file():
            return None
    except ValueError:
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _missing_repo_output(file: str) -> dict:
    return {"file": file, "error": "repo_root_not_configured_or_file_missing"}


def _language_for(file: str) -> str:
    suffix = Path(file).suffix.lower()
    if suffix in {".ets", ".ts", ".tsx"}:
        return "typescript"
    if suffix in {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp"}:
        return "cpp"
    return suffix.lstrip(".") or "unknown"


def _regex_symbols(lines: list[str]) -> list[dict]:
    symbols: list[dict] = []
    pattern = re.compile(r"^\s*(?:export\s+)?(?:(struct|class|interface|function)\s+([A-Za-z_]\w*)|([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:[:{]))")
    for idx, line in enumerate(lines, start=1):
        match = pattern.search(line)
        if not match:
            continue
        symbol_type = match.group(1) or "method"
        name = match.group(2) or match.group(3)
        symbols.append(
            {
                "type": symbol_type,
                "name": name,
                "line": idx,
                "end_line": _find_block_end(lines, idx),
                "preview": line.strip(),
            }
        )
    return symbols


def _find_block_end(lines: list[str], start_line: int) -> int:
    depth = 0
    seen_open = False
    for idx in range(start_line, len(lines) + 1):
        line = lines[idx - 1]
        depth += line.count("{")
        if "{" in line:
            seen_open = True
        depth -= line.count("}")
        if seen_open and depth <= 0:
            return idx
    return start_line


def _include_decorator_start(lines: list[str], start_line: int) -> int:
    idx = start_line - 1
    while idx > 0:
        previous = lines[idx - 1].strip()
        if previous.startswith("@"):
            idx -= 1
            continue
        break
    return idx + 1
