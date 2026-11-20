#!/usr/bin/env bash
# Build harmonyrepair:latest (base) and harmonyrepair:with-codelinter. Requires docker/command-line-tools
# (see fetch_command_line_tools.sh); the digest is verified before the 6 GB build context is sent.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$HERE/verify_command_line_tools.py" "$HERE/command-line-tools"
docker build -t harmonyrepair:latest - < "$HERE/Dockerfile.base"
docker build -t harmonyrepair:with-codelinter -f "$HERE/Dockerfile" "$HERE"
