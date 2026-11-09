#!/usr/bin/env bash
# Inside the test container: start a virtual display and PulseAudio, then run
# the shared checks, the audio, sync, merge, capture and UI tests. Prints PASS/FAIL lines.
set -u

mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
Xvfb :99 -screen 0 1280x720x24 >/dev/null 2>&1 &
pulseaudio --start --exit-idle-time=-1 >/dev/null 2>&1
for _ in $(seq 20); do pactl info >/dev/null 2>&1 && break; sleep 0.5; done

status=0
uv run --quiet python tests/snap/checks.py || status=1
uv run --quiet python tests/docker/audio_test.py || status=1
uv run --quiet python tests/docker/sync_test.py || status=1
uv run --quiet python tests/docker/merge_test.py || status=1
uv run --quiet python tests/docker/capture_test.py || status=1
uv run --quiet python tests/docker/ui_test.py || status=1

# The real app must still be running after 10 s (no crash on start).
out=$(timeout 10 uv run --quiet python app.py 2>&1); code=$?
if [ $code -eq 124 ] && ! grep -q Traceback <<<"$out"; then
  echo "PASS app starts and stays open"
else
  echo "FAIL app starts and stays open: $(grep -E 'Error|Traceback' <<<"$out" | tail -2 | tr '\n' ' ')"
  status=1
fi
exit $status
