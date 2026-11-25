# Experiment image (CodeLinter 6.0.240 + Cppcheck 2.17.1). Base is the local image built from Dockerfile.base (BASE_IMAGE=<prefix>:base);
# build.sh checks that the tag still points at the image ID it just built, so the chain is: python@sha256:6b0238... -> base image -> this image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Pinned tool versions (verified at the end of the build).
# CodeLinter 6.0.240 ships in command-line-tools 6.0.1.268 (HarmonyOS 6.0.1, API 21).
# Cppcheck 2.17.1 (Debian trixie package 2.17.1-2) comes from the base image.
ARG CODELINTER_VERSION=6.0.240
ARG CPPCHECK_VERSION=2.17.1
ARG DEBIAN_MIRROR=deb.debian.org
ARG PIP_INDEX_URL=https://pypi.org/simple
LABEL codelinter.version="${CODELINTER_VERSION}" \
      cppcheck.version="${CPPCHECK_VERSION}" \
      command-line-tools.version="6.0.1.268"

COPY command-line-tools /opt/command-line-tools

ENV DEVECO_NODE_HOME=/opt/command-line-tools/tool/node
ENV DEVECO_SDK_HOME=/opt/command-line-tools/sdk
ENV PATH=/usr/local/bin:/opt/command-line-tools/bin:${DEVECO_NODE_HOME}/bin:${PATH}

RUN sed -i "s/deb.debian.org/${DEBIAN_MIRROR}/g" /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends ripgrep=14.1.1-1+b4 \
 && rm -rf /var/lib/apt/lists/* \
 && rg --version

# Python dependencies, fully pinned (requirements.lock == `pip freeze` of the historical experiment image).
COPY requirements.lock /tmp/requirements.lock
RUN python3 -m pip install --no-cache-dir -i "${PIP_INDEX_URL}" -r /tmp/requirements.lock && rm /tmp/requirements.lock

RUN printf '%s\n' \
  '#!/usr/bin/env bash' \
  'export DEVECO_NODE_HOME=/opt/command-line-tools/tool/node' \
  'export DEVECO_SDK_HOME=/opt/command-line-tools/sdk' \
  'exec /opt/command-line-tools/bin/codelinter "$@"' \
  > /usr/local/bin/codelinter \
   && chmod 755 /usr/local/bin/codelinter \
   && printf '%s\n' \
  'export DEVECO_NODE_HOME=/opt/command-line-tools/tool/node' \
  'export DEVECO_SDK_HOME=/opt/command-line-tools/sdk' \
  'export PATH=/usr/local/bin:/opt/command-line-tools/bin:${DEVECO_NODE_HOME}/bin:${PATH}' \
  > /etc/profile.d/codelinter.sh \
   && chmod 644 /etc/profile.d/codelinter.sh \
   && test -x /opt/command-line-tools/bin/codelinter \
   && test "$(/usr/local/bin/codelinter --version | tail -n 1)" = "${CODELINTER_VERSION}" \
   && test "$(cppcheck --version)" = "Cppcheck ${CPPCHECK_VERSION}" \
   && rg --version \
   && git --version
