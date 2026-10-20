"""The control folder's files, shared with other apps on the Mac.

Everything is in ``control/`` under Sentinel's Application Support folder, which
only the user can open (0700); files are replaced atomically and owner-only (0600).
"""

from __future__ import annotations

import os
from pathlib import Path


def write_private(path: Path, text: str) -> None:
    """Replace ``path`` atomically with an owner-only file in an owner-only folder."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(text)
    os.replace(temporary, path)
