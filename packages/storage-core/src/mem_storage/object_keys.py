"""Normalisation of object keys that double as archive member names.

`market-service` packs one version's files into a zip whose members are the
normalised `object_key`s, and `application-service` maps the same keys back to
members when it reads that archive. Two hand-copied implementations drift
silently: the install would report a missing file for a manifest that is valid.
The rule therefore lives here and both services import it.
"""

from __future__ import annotations

import posixpath
from typing import Any


class ObjectKeyError(ValueError):
    """Base class for a key that cannot be normalised."""


class ObjectKeyMissing(ObjectKeyError):
    """Empty key or one containing control characters."""


class ObjectKeyInvalid(ObjectKeyError):
    """Key that escapes the bucket root."""


def normalise_object_key(object_key: Any) -> str:
    """Return a safe bucket-relative path for ``object_key``.

    - backslashes become ``/`` (packages built on Windows may contain ``\\``)
    - leading slashes are stripped: bucket keys have no absolute form
    - ``..``, empty segments, and control characters are rejected: after
      ``posixpath.normpath`` the key must still live under the root
    """

    raw = str(object_key or "")
    if not raw or any(ord(char) < 0x20 for char in raw):
        raise ObjectKeyMissing("object_key is empty or contains control characters")

    key = raw.replace("\\", "/").lstrip("/")
    normalised = posixpath.normpath(key)
    if normalised in (".", "") or normalised.startswith("../") or normalised == "..":
        raise ObjectKeyInvalid("object_key escapes the bucket root")
    return normalised
