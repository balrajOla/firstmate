from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path


class WorkbenchError(Exception):
    """An actionable error that can be shown without a traceback."""


def uid(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex


def now() -> float:
    return time.time()


def digest(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def atomic(path: Path, data: bytes | str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise WorkbenchError(f"Refusing to replace a symlink: {path}")
    fd, temp = tempfile.mkstemp(prefix=".awb-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data.encode() if isinstance(data, str) else data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def safe_text(text: str) -> str:
    # Strip OSC (including hyperlinks/clipboard) and CSI, then remaining controls.
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", str(text))
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return "".join(
        c for c in text if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) <= 159)
    )


def contained(root: Path, path: Path) -> Path:
    root, path = root.resolve(), path.resolve()
    if not path.is_relative_to(root):
        raise WorkbenchError("Path escapes its authorized root")
    return path


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise WorkbenchError(f"Invalid JSON at {path}: {exc}") from exc
