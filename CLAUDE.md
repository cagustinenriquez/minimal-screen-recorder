# screenrec-lite

Screen + app-audio recorder for Linux (Python, Tk). Published on the Snap Store
as `screenrec-lite` (~250 installs/day; most users are on Ubuntu 22.04/24.04/26.04).
Repo: github.com/cagustinenriquez/minimal-screen-recorder. Store account: `agustinenriquez`.

## Layout

- `app.py`: entry point (falls back to a basic UI if `ui.py` fails to import)
- `ui.py`: main window, settings window, recording flow
- `video.py`: screen capture loop (`ScreenRecorder`) and `merge_audio_video`. The merge
  retries once with `-c:v copy` if the H.264 re-encode fails; if both fail, `ui._keep_unmerged`
  saves `<name>.avi` + `<name>.wav` side by side and tells the user
- `wayland_capture.py`: Wayland capture via the ScreenCast portal + GStreamer pipewiresrc
- `audio.py`: routes chosen apps to a `record_sink` null sink, records it with `parec`
- `snap_compat.py`: makes `ctypes.util.find_library` work inside the snap (needed by mss)
- `config.py`: settings in `recorder_config.json`
- `snap/snapcraft.yaml`: core24 + gnome extension; `snap/gui/`: .desktop and icon
- Old/unused: `main.py`, `setup.py`, `test*.py` in the root (excluded from the snap)

## Run from source

Needs Ubuntu 24.04 / Debian 13 or newer (PyGObject needs GLib 2.80+; 22.04 users use the snap).

```bash
./install_deps.sh
uv run app.py
```

## Tests

- `tests/docker/run.sh [24.04 26.04]`: runs from source in Docker. Checks, audio routing
  (no doubled loopback, no leftovers), audio/video sync (flash + beep, must be <150 ms) and
  merge robustness (failing progress update, retry without re-encoding, no-frame video),
  Wayland capture not freezing before the first picture, and the "no picture" warning.
- `tests/snap/run-matrix.sh`: installs the built `.snap` in LXD on Ubuntu 22.04/24.04/26.04
  and Debian 13 and runs `tests/snap/checks.py` inside it.
- The real Wayland screen-share prompt can only be tested by hand on a desktop.

## Debugging user reports

- The app logs to stdout only. Started from the dock, that goes to the user journal:
  `journalctl --user --since "<time>" -o cat | grep -v "FFmpeg output"`.
- Inside the snap, `~` is `~/snap/screenrec-lite/<rev>`; use absolute paths. Reproduce with
  `snap run --shell screenrec-lite -c 'python3 /abs/path/script.py'` (app code is in `$SNAP/app`).
- A recording without sound: check `ffprobe` for an audio stream, look for a leftover
  `temp_audio_*.wav` (next to the video since 2.1.3, in the start folder before), and the
  merge lines in the journal.

## Build the snap

`snapcraft pack` (builds in LXD, project `snapcraft`). Known problems:
- "Timed out waiting for networking": Docker's iptables + UFW block `lxdbr0`. Fix:
  `sudo ufw allow in on lxdbr0` and `sudo ufw route allow in/out on lxdbr0`.
- Files removed from `snap/gui/` can stay in the package: `prime/meta/gui` in the build
  container isn't cleaned. Check with `unsquashfs -l <snap> | grep meta/gui`; remove with
  `lxc --project snapcraft exec <instance> -- rm /root/prime/meta/gui/<file>` (or `snapcraft clean`).
- Store downloads during the build are sometimes very slow; restarting helps.
- Snaps can't run inside Docker; use LXD for snap tests.

## Rules

- Anything that runs long (merging, recording) happens in a thread. Tk calls from those
  threads go through `root.after(0, ...)`, and UI problems must never stop the work itself.
- Every subprocess pipe (`stderr=PIPE` etc.) must be read while the process runs, or quieted
  (`-nostats -loglevel error`): an unread pipe fills up and stalls long recordings.
- Recording problems are often length-dependent: test with long recordings (minutes),
  not only a few seconds.
- Never commit `recorder_config.json` (local settings). Don't use `git commit -a`; add files by name.
- Work on a branch; merge into `main` with `--no-ff` when releasing.
- `TODO.md` is the (gitignored) release log: what changed, test results, release steps.
- Nothing goes to the Snap Store or GitHub releases without the user's go-ahead.
  Releases use the `release` skill (`.claude/skills/release/SKILL.md`).
- Snap specifics:
  - `audio-record` isn't auto-connected yet (forum request
    https://forum.snapcraft.io/t/screenrec-lite-auto-connect-audio-record/54086). The app
    detects it with `snapctl is-connected` and tells the user the `snap connect` command.
  - Keep the `pulseaudio` plug: `pactl load-module` may need it.
  - Inside the snap, `xdg-open` goes through `snapctl user-open`.
