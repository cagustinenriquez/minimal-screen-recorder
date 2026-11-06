#!/usr/bin/env bash
# Build one image per Ubuntu release and test the app from source in each:
# install_deps.sh + uv setup, the shared checks, an audio routing test with a
# real PulseAudio server, and the app starting.
#
# Usage:
#   tests/docker/run.sh                 # 24.04 and 26.04
#   tests/docker/run.sh 24.04           # one release

set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSIONS=("$@")
# 22.04 is not supported from source (GLib too old for PyGObject); the snap
# tests cover it.
[ ${#VERSIONS[@]} -eq 0 ] && VERSIONS=(24.04 26.04)

declare -A SUMMARY
for v in "${VERSIONS[@]}"; do
  img="screenrec-lite-test:$v"
  echo; echo "=== Ubuntu $v ==="
  if ! docker build --progress=plain -f "$ROOT/tests/docker/Dockerfile" --build-arg "UBUNTU=$v" \
       -t "$img" "$ROOT" > /tmp/screenrec-docker-build-$v.log 2>&1; then
    echo "  image build failed; last lines of /tmp/screenrec-docker-build-$v.log:"
    tail -15 /tmp/screenrec-docker-build-$v.log | sed 's/^/    /'
    SUMMARY[$v]="setup failed (install_deps.sh or uv sync)"
    continue
  fi
  results=$(docker run --rm "$img" 2>&1 | grep -E '^(PASS|FAIL) ')
  echo "$results" | sed 's/^/    /'
  SUMMARY[$v]="$(grep -c '^PASS' <<<"$results") passed, $(grep -c '^FAIL' <<<"$results") failed"
done

echo; echo "=== Summary ==="
status=0
for v in "${VERSIONS[@]}"; do
  printf '  Ubuntu %-8s %s\n' "$v" "${SUMMARY[$v]}"
  [[ "${SUMMARY[$v]}" == *", 0 failed" ]] || status=1
done
exit $status
