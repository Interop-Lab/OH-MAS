#!/usr/bin/env bash
# Install HarmonyOS command-line-tools 6.0.1.268 (linux-x64, provides CodeLinter 6.0.240) into docker/command-line-tools.
#
# The archive is distributed by Huawei behind a developer login, so it cannot be fetched anonymously:
#   1. Download "Command Line Tools" 6.0.1.268 (linux-x64) from https://developer.huawei.com/consumer/cn/download/
#   2. Run:  docker/fetch_command_line_tools.sh /path/to/commandline-tools-linux-x64-6.0.1.268.zip [ARCHIVE_SHA256]
#      (a direct http(s) URL is also accepted in place of the path)
# ARCHIVE_SHA256 is optional; the extracted tree is always checked against command-line-tools.tree.sha256.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1:?usage: $0 <archive-path-or-url> [archive-sha256]}"
SHA="${2:-}"
DEST="$HERE/command-line-tools"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

if [[ "$SRC" =~ ^https?:// ]]; then
  ARCHIVE="$WORK/$(basename "${SRC%%\?*}")"
  curl -fL --retry 3 -o "$ARCHIVE" "$SRC"
else
  ARCHIVE="$SRC"
fi
[[ -f "$ARCHIVE" ]] || { echo "archive not found: $ARCHIVE" >&2; exit 2; }

if [[ -n "$SHA" ]]; then
  echo "$SHA  $ARCHIVE" | sha256sum -c -
else
  echo "archive sha256: $(sha256sum "$ARCHIVE" | cut -d' ' -f1)  (no checksum given; relying on tree digest)"
fi

mkdir -p "$WORK/x"
case "$ARCHIVE" in
  *.zip) unzip -q "$ARCHIVE" -d "$WORK/x" ;;
  *.tar.gz|*.tgz) tar -xzf "$ARCHIVE" -C "$WORK/x" ;;
  *.tar) tar -xf "$ARCHIVE" -C "$WORK/x" ;;
  *) echo "unsupported archive type: $ARCHIVE" >&2; exit 2 ;;
esac
ROOT="$WORK/x"
[[ -f "$ROOT/bin/codelinter" ]] || ROOT="$(dirname "$(dirname "$(find "$WORK/x" -path '*/bin/codelinter' | head -n1)")")"

python3 "$HERE/verify_command_line_tools.py" "$ROOT"
rm -rf "$DEST"; mv "$ROOT" "$DEST"
echo "installed to $DEST"
