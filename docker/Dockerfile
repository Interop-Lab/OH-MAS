  FROM harmonyrepair:latest

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
   && /usr/local/bin/codelinter --version \
   && rg --version \
   && cppcheck --version \
   && git --version
