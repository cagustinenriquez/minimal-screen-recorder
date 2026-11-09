---
name: release
description: Release a new version of screenrec-lite to the Snap Store and GitHub (bump version, build, verify, upload, progressive stable release, merge to main, tag, GitHub release, TODO.md). Use when the user asks to release, publish or ship a version, or to "upload" a build.
---

# Release screenrec-lite

Input: the version to release (e.g. `2.1.3`) and the branch with the changes.
If the user names a version that is already released (`snapcraft revisions screenrec-lite`),
ask whether they mean the next one instead of uploading anything.

## 1. Prepare (no public effects)

1. Check the state: `git status --short` (only `recorder_config.json` may be modified),
   `snapcraft whoami` (logged in, not expired), `snapcraft status screenrec-lite`.
2. On the release branch, set `version: '<version>'` in `snap/snapcraft.yaml`, commit
   (`git add snap/snapcraft.yaml`; never `git commit -a`), push the branch.
3. Run the source tests if code changed: `tests/docker/run.sh` (all must pass).
4. Build: `snapcraft pack` (takes minutes; run in background).
5. Verify the package before uploading:
   - `unsquashfs -l screenrec-lite_<version>_amd64.snap | grep meta/gui`: only the current
     icon/desktop files (stale files: see CLAUDE.md "Build the snap")
   - `unsquashfs -cat <snap> app/<file>.py | grep <something from the change>`: the change is in it
   - `sha256sum <snap>`
6. Offer a local test before releasing:
   `sudo snap install --dangerous ./screenrec-lite_<version>_amd64.snap` and
   `sudo snap connect screenrec-lite:audio-record`. Afterwards the user goes back to the store
   copy with `sudo snap refresh screenrec-lite --amend` (plain `refresh` fails with
   "unknown to the store").

## 2. Release (public: only after the user says go)

Confirm the version and rollout once, then:

1. Merge and tag:
   ```bash
   git checkout main && git pull --ff-only origin main
   git merge --no-ff <branch> -m "Release <version>: <summary>"
   git tag -a v<version> -m "screenrec-lite <version>"
   git push origin main v<version>
   ```
2. Upload and release (80% first unless the user says otherwise):
   ```bash
   snapcraft upload screenrec-lite_<version>_amd64.snap      # prints "Revision N created"
   snapcraft release screenrec-lite <N> stable --progressive 80
   snapcraft status screenrec-lite
   ```
   A new progressive release replaces any rollout still in progress.
3. GitHub release: write notes for users (what's new, in plain words; install commands at
   the top; link the previous release for older changes), then
   `gh release create v<version> --verify-tag --latest --title "screenrec-lite <version>" --notes-file <file>`.
   Don't attach the `.snap`; users install from the store.
4. Add a "Release <version> (<date>)" section at the top of the release log in `TODO.md`:
   what changed, merge commit, revision, rollout, the command to go to 100%
   (`snapcraft release screenrec-lite <N> stable`) and the rollback command (previous revision).

## 3. Report

Tell the user: revision number, rollout %, GitHub release link, and the follow-ups
(go to 100% after a day or two without problems; rollback command).
