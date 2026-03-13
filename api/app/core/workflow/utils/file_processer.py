# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/3/10 13:36
TRANSFORM_FILE_TYPE = {
    'text/plain': 'document/text',
    'text/markdown': 'document/markdown',
    'text/x-markdown': 'document/x-markdown',

    'application/pdf': 'document/pdf',

    'application/msword': 'document/doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'document/docx',

    'application/vnd.ms-powerpoint': 'document/ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'document/pptx',
}
ALLOWED_FILE_TYPES = [
    'text/plain',
    'text/markdown',
    'text/x-markdown',
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'image/jpg',
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/bmp',
    'image/webp',
    'image/svg+xml',
    'video/mp4',
    'video/quicktime',
    'video/x-msvideo',
    'video/x-matroska',
    'video/webm',
    'video/x-flv',
    'video/x-ms-wmv',
    'audio/mpeg',
    'audio/wav',
    'audio/ogg',
    'audio/aac',
    'audio/flac',
    'audio/mp4',
    'audio/x-ms-wma',
    'audio/x-m4a',
]


def mime_to_file_type(mime_type):
    if mime_type not in ALLOWED_FILE_TYPES:
        return None

    return TRANSFORM_FILE_TYPE.get(mime_type, mime_type)
