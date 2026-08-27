"""HTTP transfer profiles for knowledge route forwarding."""

from enum import StrEnum


class CallProfile(StrEnum):
    JSON = "json"
    MULTIPART_UPLOAD = "multipart_upload"
    STREAM_DOWNLOAD = "stream_download"
