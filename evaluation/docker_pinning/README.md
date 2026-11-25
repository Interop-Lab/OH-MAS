# Docker base image pinning and build verification

Created 2026-11-22. No model calls, no experiment reruns; the paper, the results and the images in use (`harmonyrepair:latest`, `harmonyrepair:with-codelinter`, ...) were not changed. The repository's own `docker/harmonyrepair-codelinter/Dockerfile` is untouched (sha256 in `evidence/original_Dockerfile.sha256`).

## Files
- `files/Dockerfile.base`, `files/Dockerfile`, `files/build.sh`, `files/requirements.base.lock`, `files/requirements.lock`, `files/command_line_tools_tree_sha256.txt`: the new pinned build environment. `Dockerfile.base` and `build.sh` did not exist in the repository; they are new (the base was reconstructed from `docker history`).
- `verification/`: `build_success.log` (full successful build), `build_report.txt`, `versions_new.txt`, `new_dpkg_all.txt`, `new_pip_freeze.txt`, `new_tools_tree_sha256.txt`, `base.iid`, `final.iid`, plus the two earlier attempts (`build_attempt1_aborted_slow_official_mirror.log`, `build_attempt2_failed_FROM_imageid.log`, kept for transparency).
- `evidence/`: data from the historical experiment images (inspect summary, version/package lists, tools-tree hashes, Docker Hub manifest of the pinned digest).
- `SHA256SUMS`: SHA-256 of every file here.
Rebuild: `cd <dir containing files/* and command-line-tools/>; ./build.sh [tag-prefix]` (set `BUILD_ARGS='--build-arg DEBIAN_MIRROR=mirrors.tuna.tsinghua.edu.cn --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple'` to use the mirrors of the historical build; the build here used them because the default Debian mirror was too slow from this server).

## Pinned base image
`FROM python:3.11@sha256:6b023894c26ea77c602814d8b682507e1f9dd731038e25f93381b488ca9e7176` (OCI index; linux/amd64 manifest `sha256:a8c1d6b97bc1babb60ae5a72ee60e9fca8bf0054accc0d5ca4745370c5d9d2de`, image ID `e22fa15c9daa...`, Debian 13 trixie, Python 3.11.14). The digest was confirmed to exist on Docker Hub with `docker manifest inspect` (`evidence/python311_manifest_index.json`), and the amd64 manifest's config digest equals the local image ID.

## A. Current pinned build verified (success)
Build exit 0 (`verification/build_success.log`, `build_report.txt`). New images: base `4bf704409b7a`, experiment `490238cbd425` (full IDs in `*.iid`). Inside the new image: CodeLinter 6.0.240, Cppcheck 2.17.1 (apt 2.17.1-2), ripgrep 14.1.1, git 2.47.3, Python 3.11.14, Debian 13, x86_64. The build itself asserts CodeLinter and Cppcheck versions (Dockerfile final step). `build.sh` first checks the command-line-tools tree hash and refuses to build on mismatch. Base is chained python digest -> local base image (ID checked) -> experiment image. apt versions of cppcheck, clang-tidy, ripgrep are pinned with `=`; pip packages come from complete lock files.

## B. Proven consistent with the historical experiment image (`harmonyrepair:with-codelinter`, `f825fefcb2a2`, built 2026-05-25)
- Upstream: the first 7 layers of `harmonyrepair:latest` (`07b805e2d87f`, the historical base) equal `python:3.11` (`sha256:6b0238...`) layer for layer; the historical experiment image shares all 10 layers of that base.
- Tool versions identical: CodeLinter 6.0.240, Cppcheck 2.17.1 (2.17.1-2), ripgrep 14.1.1-1+b4, git 2.47.3, Python 3.11.14.
- `command-line-tools` (CodeLinter 6.0.1.268): 95,883 files, per-file SHA-256 list identical between the build context, the historical image and the new image (tree hash `2689464b...`).
- pip: `pip freeze` of the new image equals the historical one (78 packages, 0 differences); the repository's `requirements.lock` equals it too.
- apt: 487 packages, 486 identical.

## C. Not proven / missing
- One apt package differs: `python3-pygments` 2.18.0+dfsg-2 (historical) vs 2.18.0+dfsg-2+deb13u1 (new). It is an unpinned transitive dependency that received a Debian security update; the older version is no longer served by the normal mirrors. Pinning all transitive packages would need `snapshot.debian.org` (the base python image's source list comments mention `20260202T000000Z`); not done.
- The new image is a rebuild, not the same bytes: its layers and image ID differ from the historical image (`490238cbd425` vs `f825fefcb2a2`). Consistency is shown at tool/package/file level, not at layer level.
- The historical base `harmonyrepair:latest` was built locally (no registry digest), and its `Dockerfile.base`/`build.sh` were not found. The historical pip install was unpinned (`>=`); what it resolved is known only from `pip freeze`. Historical mirrors (Tsinghua) and the package index state at build time cannot be reconstructed.
- The license basis and the download-verification scheme for CodeLinter / command-line-tools that you mentioned as "confirmed" could not be located in the repository or notes available here; the official download archive's checksum is also not available. Only the extracted tree hash is recorded.
- The build used network mirrors at build time; offline rebuild is not supported.
