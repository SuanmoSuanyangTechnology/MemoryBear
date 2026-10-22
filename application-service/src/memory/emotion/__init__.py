"""Emotion reply domain (reply pipeline produces emotion; fast-write pipeline consumes the cache).

- ``emotion_cache``: Redis emotion cache (written by the reply pipeline,
  read-and-delete by the fast-write pipeline).
- ``emotion_resolver``: reply-side emotion detection + policy mapping + prompt injection.
"""
