"""Write the raw payload to disk so the footer can point at it. Never raises."""
from __future__ import annotations

import errno
import hashlib
import os
import tempfile


def resolve_dir(sink: str | None) -> str:
    if sink:
        return sink
    env = os.environ.get("LDM_COMPRESS_DIR")
    if env:
        return env
    if os.path.isdir("/tool-results") and os.access("/tool-results", os.W_OK):
        return "/tool-results"
    return os.path.join(tempfile.gettempdir(), "ldm-compress")


def persist(raw: bytes, preset_name: str, sink: str | None) -> tuple[str | None, str | None]:
    """Returns (path, error_reason). Never raises."""
    try:
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "replace")
        directory = resolve_dir(sink)
        # Extension follows the payload, not the preset: JSON-looking bytes get .json.
        ext = "json" if raw.lstrip()[:1] in (b"{", b"[") else "txt"
        digest = hashlib.sha256(raw).hexdigest()
        path = os.path.join(directory, f"{preset_name}-{digest[:8]}.{ext}")
        os.makedirs(directory, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw)
        return path, None
    except OSError as exc:
        return None, errno.errorcode.get(exc.errno, str(exc.errno))
    except Exception as exc:  # pragma: no cover - defensive
        return None, type(exc).__name__
