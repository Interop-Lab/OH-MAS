#!/usr/bin/env python3
"""Verify (or --write) the content digest of an extracted HarmonyOS command-line-tools tree.

The digest is the SHA-256 over sorted lines "<type> <exec-bit> <sha256-or-link-target> <relpath>",
so it is independent of archive format, mtimes and owners.

  python3 docker/verify_command_line_tools.py [TOOLS_DIR]          # verify against command-line-tools.tree.sha256
  python3 docker/verify_command_line_tools.py [TOOLS_DIR] --write  # (re)create the expected digest
"""
import hashlib, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECTED_FILE = HERE / "command-line-tools.tree.sha256"
EXPECTED_VERSION = "6.0.1.268"


def file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(root: Path) -> tuple[str, int]:
    lines = []
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for name in sorted(fns + [d for d in dns if (Path(dp) / d).is_symlink()]):
            p = Path(dp) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                lines.append(f"l 0 {os.readlink(p)} {rel}")
            else:
                lines.append(f"f {int(os.access(p, os.X_OK))} {file_sha256(p)} {rel}")
    lines.sort()
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), len(lines)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(args[0]) if args else HERE / "command-line-tools"
    if not (root / "bin" / "codelinter").exists():
        print(f"ERROR: {root}/bin/codelinter not found", file=sys.stderr); return 2
    version_txt = (root / "version.txt").read_text() if (root / "version.txt").exists() else ""
    if EXPECTED_VERSION not in version_txt:
        print(f"ERROR: {root}/version.txt does not mention command-line-tools {EXPECTED_VERSION}", file=sys.stderr); return 2
    digest, n = tree_digest(root)
    if "--write" in sys.argv:
        EXPECTED_FILE.write_text(f"{digest}  {n} entries  command-line-tools {EXPECTED_VERSION}\n")
        print(f"wrote {EXPECTED_FILE.name}: {digest} ({n} entries)"); return 0
    expected = EXPECTED_FILE.read_text().split()[0]
    if digest != expected:
        print(f"ERROR: tree digest mismatch\n  expected {expected}\n  actual   {digest} ({n} entries)", file=sys.stderr); return 1
    print(f"OK: command-line-tools {EXPECTED_VERSION} matches expected digest ({n} entries)"); return 0


if __name__ == "__main__":
    sys.exit(main())
