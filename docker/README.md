# Docker build and verification

Run these instructions on Linux/amd64 with Docker, Bash, Python 3, curl (for a URL input), tar/unzip, GNU findutils/coreutils, and xargs available. The command-line-tools binaries are a separate build input and are not included in this repository.

## Build from the repository root

```bash
bash docker/fetch_command_line_tools.sh /path/to/commandline-tools-linux-x64-6.0.1.268.zip
bash docker/build.sh harmonyrepair
```

The first command extracts the vendor tool archive into `docker/command-line-tools/` and uses the existing `verify_command_line_tools.py` plus `command-line-tools.tree.sha256` to validate the normalized tree. An optional second argument is an expected archive SHA-256 if one is independently available; the repository does not claim to provide the vendor's original archive checksum.

The build script changes its working directory to `docker/` and verifies the tool contents against `command_line_tools_tree_sha256.txt`, using the same per-file-list algorithm as the server build. The required build files are listed below. The two tool hash files use distinct algorithms and are not interchangeable.

The build context for both Docker stages is `docker/`:

| Stage | Required COPY inputs |
|---|---|
| `Dockerfile.base` | `requirements.base.lock` |
| `Dockerfile` | `requirements.lock`, `command-line-tools/` |

`Dockerfile.base` uses `python:3.11@sha256:6b023894c26ea77c602814d8b682507e1f9dd731038e25f93381b488ca9e7176`. After building the local base, the script checks its tag still identifies the newly built image before supplying `BASE_IMAGE` to stage two. Use the script for the two-stage build rather than invoking stage two without its required build argument.

The explicit prefix `harmonyrepair` creates `harmonyrepair:base` and `harmonyrepair:with-codelinter`. The final tag matches the supplied runtime configs. The script's no-argument prefix is `harmonyrepair-pinned`; if using it, set `runtime.docker_image` to `harmonyrepair-pinned:with-codelinter`. Tags under the selected prefix will be updated by a build.

Optional mirrors, as used for the server verification:

```bash
BUILD_ARGS='--build-arg DEBIAN_MIRROR=mirrors.tuna.tsinghua.edu.cn --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple' bash docker/build.sh harmonyrepair
```

The default download sources remain Debian and PyPI. Network access is needed. Base and final image IDs and version checks are written to `docker/base.iid`, `docker/final.iid`, and `docker/build_report.txt`.

## Verification provenance

The original server bundle is unchanged under [`evaluation/docker_pinning/`](../evaluation/docker_pinning/README.md), including its SHA256SUMS, successful build log, package inventories, and image IDs. Its references to the original server repository layout describe that server snapshot; active repository build instructions are this file and the root README.

All six promoted build files are byte-identical to that verified delivery. This integration was checked locally for paths, file references and hashes. No local shell execution, Docker build or model call was performed; successful-build evidence is the preserved server log.

Historical comparison found matching CodeLinter/Cppcheck versions, matching tool-file content lists and 78 matching pip packages, with one of 487 apt packages different (`python3-pygments`). This is not a claim of byte-identical historical images or fully frozen apt transitive dependencies.
