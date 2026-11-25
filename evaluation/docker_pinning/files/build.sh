#!/usr/bin/env bash
# Build base -> experiment image from digest-pinned sources and verify tool versions.
# Usage: ./build.sh [TAG_PREFIX] ; build context = this directory + a `command-line-tools/` directory (CodeLinter 6.0.1.268).
set -euo pipefail
cd "$(dirname "$0")"
P="${1:-harmonyrepair-pinned}"
EXPECT_TREE="$(cat command_line_tools_tree_sha256.txt)"
echo "[1/4] verifying command-line-tools tree (C-locale sorted per-file sha256 list)"
GOT="$(cd command-line-tools && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -P4 -n200 sha256sum | LC_ALL=C sort -k2 | sha256sum | cut -d' ' -f1)"
[ "$GOT" = "$EXPECT_TREE" ] || { echo "command-line-tools tree mismatch: $GOT != $EXPECT_TREE"; exit 1; }
echo "[2/4] base image from Dockerfile.base (FROM python:3.11@sha256:6b0238...)"
docker build -f Dockerfile.base -t "$P:base" --iidfile base.iid ${BUILD_ARGS:-} .
echo "[3/4] experiment image"
[ "$(docker image inspect "$P:base" --format '{{.Id}}')" = "$(cat base.iid)" ] || { echo "base tag moved"; exit 1; }
docker build -f Dockerfile --build-arg BASE_IMAGE="$P:base" -t "$P:with-codelinter" --iidfile final.iid ${BUILD_ARGS:-} .
echo "[4/4] verification"
{
  echo "base image id:  $(cat base.iid)"; echo "final image id: $(cat final.iid)"
  docker run --rm --network none "$P:with-codelinter" bash -c 'codelinter --version | tail -n 1; cppcheck --version; rg --version | head -1; git --version; python3 --version; dpkg-query -W -f="\${Package}=\${Version}\n" cppcheck clang-tidy ripgrep git'
} | tee build_report.txt
