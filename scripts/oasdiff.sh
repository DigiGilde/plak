#!/usr/bin/env bash
# oasdiff as a command, run from the image pinned in
# .github/oasdiff/Containerfile, the one CI copies its binary from. Takes
# the arguments oasdiff takes, so `release.py prepare --oasdiff scripts/oasdiff.sh`
# works as it does with the binary in CI. A file argument is mounted
# read-only at its own absolute path; the container gets no network.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v podman >/dev/null 2>&1; then
    echo "podman not found on PATH; install podman to run oasdiff." >&2
    exit 1
fi

image="$(grep -oE '^FROM docker\.io/tufin/oasdiff:[^@ ]+@sha256:[0-9a-f]{64}$' \
    "${REPO_ROOT}/.github/oasdiff/Containerfile" | cut -d' ' -f2 || true)"
if [[ -z "${image}" ]]; then
    echo "No oasdiff image pinned by digest in .github/oasdiff/Containerfile." >&2
    exit 1
fi

args=()
mounts=()
for arg in "$@"; do
    if [[ -f "${arg}" ]]; then
        dir="$(cd "$(dirname "${arg}")" && pwd)"
        arg="${dir}/$(basename "${arg}")"
        mounts+=(--volume "${dir}:${dir}:ro")
    fi
    args+=("${arg}")
done

exec podman run --rm --network none --entrypoint /usr/bin/oasdiff \
    ${mounts[@]+"${mounts[@]}"} "${image}" ${args[@]+"${args[@]}"}
