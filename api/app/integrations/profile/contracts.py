"""Transport-neutral contracts for the memory profile integration."""

from __future__ import annotations

from enum import StrEnum


class MemoryProfileAuthSource(StrEnum):
    """Which face the caller arrived on — the authentication mode.

    ``APIKEY`` is traffic that arrived through the API-key face, ``INTERNAL``
    everything that reached us as an internal call (a user JWT terminated
    upstream, a service-to-service call). This is the value of the generic
    ``x-source`` header; the internal token's ``auth_type`` claim keeps its own
    ``api_key`` / absent spelling and is mapped onto these two.

    The face also decides *what* identifies the caller: ``APIKEY`` sends the key
    that authenticated, as ``x-api-key-id``, and every other face sends the user,
    as ``x-user-id`` — the same header pair the Gateway injects for the face it
    terminated, so a request means the same thing whichever hop terminated it.
    The key id is not the creator's user id, and the two must not be swapped.
    """

    APIKEY = "ex_api"
    INTERNAL = "in_api"
