r"""Wire protocol shared by the zygote worker and the server-side manager.

A frame is::

    | payload_len (uint32) | msg_type (uint8) | req_id (uint32) | payload |
      \_________________________ 9-byte header ________________________/

``payload_len`` counts only the payload bytes that follow the header.

The protocol is intentionally tiny and stdlib-only so the zygote worker never
has to import the ``app`` package.
"""
import struct

HEADER = struct.Struct("!IBI")  # payload_len, msg_type, req_id
HEADER_SIZE = HEADER.size  # 9

# Server -> zygote
MSG_RUN = 1     # payload: JSON {code, key, uid, gid, net}
MSG_KILL = 2    # payload: empty

# Zygote -> server
MSG_STDOUT = 3  # payload: raw bytes
MSG_STDERR = 4  # payload: raw bytes
MSG_DONE = 5    # payload: int32 exit_code (os.waitstatus_to_exitcode semantics)

DONE_STRUCT = struct.Struct("!i")


def encode_frame(msg_type: int, req_id: int, payload: bytes = b"") -> bytes:
    """Serialize a single frame."""
    return HEADER.pack(len(payload), msg_type, req_id) + payload
