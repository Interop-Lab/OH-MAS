  FROM harmonyrepair:latest

  # Pinned tool versions (verified at the end of the build).
  # CodeLinter 6.0.240 ships in command-line-tools 6.0.1.268 (HarmonyOS 6.0.1, API 21).
  # Cppcheck 2.17.1 (Debian trixie package 2.17.1-2) comes from the base image.
  ARG CODELINTER_VERSION=6.0.240
  ARG CPPCHECK_VERSION=2.17.1
  LABEL codelinter.version="${CODELINTER_VERSION}" \
        cppcheck.version="${CPPCHECK_VERSION}" \
        command-line-tools.version="6.0.1.268"

  COPY command-line-tools /opt/command-line-tools

  ENV DEVECO_NODE_HOME=/opt/command-line-tools/tool/node
  ENV DEVECO_SDK_HOME=/opt/command-line-tools/sdk
  ENV PATH=/usr/local/bin:/opt/command-line-tools/bin:${DEVECO_NODE_HOME}/bin:${PATH}

  RUN apt-get update \
   && apt-get install -y --no-install-recommends ripgrep \
   && rm -rf /var/lib/apt/lists/* \
   && rg --version

  # Pre-install Python dependencies to avoid downloading them on every container run.
# litellm pulls in openai, jinja2, pydantic, python-dotenv, httpx, tiktoken, etc.
# 'datasets' is excluded — not used at runtime.
RUN python3 -m pip install --no-cache-dir \
    "litellm>=1.75.5,!=1.82.7,!=1.82.8" \
    pyyaml \
    requests \
    tenacity \
    rich \
    typer \
    platformdirs \
    textual \
    prompt_toolkit

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
