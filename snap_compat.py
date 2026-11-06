"""Fixes for running inside a snap. Import before mss.

ctypes.util.find_library() only consults the base snap's linker cache, which
doesn't know about libraries shipped in this snap or its GNOME runtime (e.g.
libxcb-render). mss looks up its X11 libraries that way, so X11 capture failed
with "Library libxcb-render.so not found". When running as a snap, fall back to
searching LD_LIBRARY_PATH for the library.
"""

import ctypes.util
import glob
import os
import re

_original_find_library = ctypes.util.find_library


def _find_in_library_path(name: str) -> str | None:
    for directory in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
        if not directory:
            continue
        candidates = sorted(glob.glob(os.path.join(directory, f"lib{name}.so*")))
        # Prefer the versioned runtime name (libfoo.so.0) over dev symlinks.
        for path in candidates:
            if re.fullmatch(rf"lib{re.escape(name)}\.so\.\d+", os.path.basename(path)):
                return os.path.basename(path)
        if candidates:
            return os.path.basename(candidates[0])
    return None


def _find_library(name: str) -> str | None:
    return _original_find_library(name) or _find_in_library_path(name)


if os.environ.get("SNAP") and ctypes.util.find_library is _original_find_library:
    ctypes.util.find_library = _find_library
