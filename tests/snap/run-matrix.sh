#!/usr/bin/env bash
# Install the built snap on several Linux distributions (LXD containers) and
# check it works inside its sandbox: libraries, ffmpeg/pactl/GStreamer, the
# window opening, X11 capture, and the real app starting without crashing.
#
# Usage:
#   tests/snap/run-matrix.sh                       # all systems
#   tests/snap/run-matrix.sh ubuntu:24.04 images:debian/13
#   KEEP=1 tests/snap/run-matrix.sh ...            # keep containers afterwards
#
# Needs: LXD (user in the lxd group) and a built screenrec-lite_*.snap.
# The snaps it depends on (GNOME runtime etc.) are downloaded once into
# tests/snap/.cache and reused, because the Snap Store can be very slow.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$ROOT/tests/snap"
CACHE="$HERE/.cache"
SNAP_FILE="${SNAP_FILE:-$(ls -t "$ROOT"/screenrec-lite_*_amd64.snap 2>/dev/null | head -1)}"
SYSTEMS=("$@")
[ ${#SYSTEMS[@]} -eq 0 ] && SYSTEMS=(ubuntu:22.04 ubuntu:24.04 ubuntu:26.04 images:debian/13)
# Fedora (VM; slow and its setup was unreliable): tests/snap/run-matrix.sh images:fedora/44

# Snaps screenrec-lite needs at runtime, in install order.
DEPS=(snapd core24 bare gtk-common-themes mesa-2404 gnome-46-2404)

[ -f "$SNAP_FILE" ] || { echo "No snap found. Build it first: snapcraft pack"; exit 1; }
echo "Testing $(basename "$SNAP_FILE") on: ${SYSTEMS[*]}"

# ---- download dependency snaps once ----------------------------------------
mkdir -p "$CACHE"
for s in "${DEPS[@]}"; do
  if ! ls "$CACHE/${s}"_*.snap >/dev/null 2>&1; then
    echo "Downloading $s (once; cached in tests/snap/.cache)..."
    (cd "$CACHE" && snap download "$s" >/dev/null) || { echo "Download of $s failed"; exit 1; }
  fi
done

log() { echo "  [$1] $2"; }

# ---- per-system setup -------------------------------------------------------
prepare() { # $1=container $2=image
  local c=$1 img=$2
  case "$img" in
    ubuntu:*)
      lxc exec "$c" -- sh -c 'cloud-init status --wait >/dev/null 2>&1; apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xvfb >/dev/null' ;;
    images:debian/*)
      lxc exec "$c" -- sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq snapd squashfuse fuse3 xvfb >/dev/null && systemctl start snapd.socket snapd.service' ;;
    images:fedora/*)
      # The VM image doesn't grow its root partition to the disk size on boot.
      lxc exec "$c" -- sh -c 'dnf install -y -q cloud-utils-growpart e2fsprogs >/dev/null && growpart /dev/sda 2 >/dev/null; case $(findmnt -no FSTYPE /) in btrfs) btrfs filesystem resize max / ;; xfs) xfs_growfs / ;; *) resize2fs "$(findmnt -no SOURCE /)" ;; esac >/dev/null 2>&1'
      lxc exec "$c" -- sh -c 'dnf install -y -q snapd xorg-x11-server-Xvfb >/dev/null && ln -sf /var/lib/snapd/snap /snap && systemctl enable --now snapd.socket >/dev/null 2>&1 && systemctl start snapd.service' ;;
  esac
}

wait_network() { # $1=container
  for _ in $(seq 180); do
    lxc exec "$1" -- getent hosts archive.ubuntu.com >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

install_snaps() { # $1=container
  local c=$1 s f
  lxc exec "$c" -- snap wait system seed.loaded >/dev/null 2>&1
  lxc exec "$c" -- mkdir -p /root/snaps
  for s in "${DEPS[@]}"; do
    for f in "$CACHE/${s}"_*; do lxc file push "$f" "$c/root/snaps/" >/dev/null; done
    lxc exec "$c" -- sh -c "snap ack /root/snaps/${s}_*.assert && snap install /root/snaps/${s}_*.snap >/dev/null" \
      || { log "$c" "installing $s failed"; return 1; }
  done
  lxc file push "$SNAP_FILE" "$c/root/snaps/app.snap" >/dev/null
  lxc exec "$c" -- snap install --dangerous /root/snaps/app.snap >/dev/null \
    || { log "$c" "installing screenrec-lite failed"; return 1; }
  # Same permission a store install needs approved; grant it for the test.
  lxc exec "$c" -- snap connect screenrec-lite:audio-record >/dev/null 2>&1
  return 0
}

run_checks() { # $1=container
  local c=$1
  lxc exec "$c" -- sh -c 'nohup Xvfb :99 -screen 0 1280x720x24 >/dev/null 2>&1 &'
  sleep 2
  lxc exec "$c" -- mkdir -p /var/snap/screenrec-lite/common
  lxc file push "$HERE/checks.py" "$c/var/snap/screenrec-lite/common/checks.py" >/dev/null
  lxc exec "$c" --env DISPLAY=:99 -- snap run --shell screenrec-lite \
    -c '$SNAP/bin/python3 /var/snap/screenrec-lite/common/checks.py' 2>&1 | grep -E '^(PASS|FAIL) '

  # The real app: it must still be running after 10 s (no crash on start).
  local out code
  out=$(lxc exec "$c" --cwd /var/snap/screenrec-lite/common --env DISPLAY=:99 -- \
        timeout 10 snap run screenrec-lite 2>&1); code=$?
  if [ $code -eq 124 ] && ! grep -q Traceback <<<"$out"; then
    echo "PASS app starts and stays open"
  else
    echo "FAIL app starts and stays open: $(grep -E 'Error|Traceback' <<<"$out" | tail -2 | tr '\n' ' ')"
  fi
}

# ---- main -------------------------------------------------------------------
declare -A SUMMARY
for img in "${SYSTEMS[@]}"; do
  c="srl-$(tr -c 'a-z0-9' '-' <<<"${img#*:}" | sed 's/-*$//')"
  echo; echo "=== $img ($c) ==="
  lxc delete --force "$c" >/dev/null 2>&1
  # Fedora can't mount snaps inside a container (its squashfuse lacks LZO),
  # so it runs as a small VM with a real kernel, like a real install.
  case "$img" in
    images:fedora/*) launch_opts=(--vm -c limits.cpu=2 -c limits.memory=3GiB -d root,size=20GiB) ;;
    *)               launch_opts=(-c security.nesting=true) ;;
  esac
  if ! lxc launch "$img" "$c" "${launch_opts[@]}" >/dev/null; then
    SUMMARY[$img]="could not start container"; continue
  fi
  if ! wait_network "$c"; then SUMMARY[$img]="no network in container"; continue; fi
  log "$c" "setting up"
  prepare "$c" "$img" || { SUMMARY[$img]="setup failed"; continue; }
  log "$c" "installing snaps"
  install_snaps "$c" || { SUMMARY[$img]="snap install failed"; continue; }
  log "$c" "running checks"
  results=$(run_checks "$c")
  echo "$results" | sed 's/^/    /'
  fails=$(grep -c '^FAIL' <<<"$results")
  passes=$(grep -c '^PASS' <<<"$results")
  SUMMARY[$img]="$passes passed, $fails failed"
  [ "${KEEP:-0}" = 1 ] || lxc delete --force "$c" >/dev/null 2>&1
done

echo; echo "=== Summary ==="
status=0
for img in "${SYSTEMS[@]}"; do
  printf '  %-20s %s\n' "$img" "${SUMMARY[$img]}"
  [[ "${SUMMARY[$img]}" == *", 0 failed" ]] || status=1
done
exit $status
