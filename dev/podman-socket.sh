#!/usr/bin/env bash
# Prints the DOCKER_HOST value that points testcontainers at the Podman
# socket: `export DOCKER_HOST="$(dev/podman-socket.sh)"`.
set -euo pipefail

if [[ "$(uname -s)" == "Darwin" ]]; then
    # macOS: no native Podman socket, so via the podman machine
    socket="$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}' 2>/dev/null || true)"
    if [[ -z "${socket}" ]]; then
        # fallback: default location of the podman machine socket
        socket="${HOME}/.local/share/containers/podman/machine/podman.sock"
    fi
    echo "unix://${socket}"
else
    echo "unix://${XDG_RUNTIME_DIR}/podman/podman.sock"
fi
