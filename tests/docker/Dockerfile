# Runs the app from source on a given Ubuntu release, set up the way the
# README says (install_deps.sh, then uv), plus a virtual display and a
# PulseAudio server so the checks and the audio routing test can run.
#
# Build from the repository root:
#   docker build -f tests/docker/Dockerfile --build-arg UBUNTU=24.04 -t screenrec-lite-test:24.04 .

ARG UBUNTU=24.04
FROM ubuntu:${UBUNTU}

ENV DEBIAN_FRONTEND=noninteractive

# tzdata is installed here (non-interactive) so it can't stop a later apt run
# to ask for a time zone. Test-only tools: sudo (install_deps.sh uses it), a virtual X display, a
# PulseAudio server, and a compiler for building PyGObject. libgl1 is needed
# by opencv-python and is present on any desktop install.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      sudo ca-certificates tzdata xvfb pulseaudio build-essential libgl1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /usr/local/bin/

RUN useradd -m tester \
 && echo 'tester ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/tester \
 && echo 'Defaults env_keep += "DEBIAN_FRONTEND"' > /etc/sudoers.d/keep-frontend
USER tester
WORKDIR /home/tester/app

# Step 1 of the README: system dependencies.
COPY --chown=tester install_deps.sh ./
RUN ./install_deps.sh

# Step 2 of the README: Python dependencies (uv run does this on first use).
COPY --chown=tester . .
RUN uv sync

ENV APP_DIR=/home/tester/app \
    DISPLAY=:99 \
    XDG_RUNTIME_DIR=/tmp/runtime-tester

CMD ["tests/docker/entry.sh"]
