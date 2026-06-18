from .context import build_chunk_context
from .router import FileTypeRouter


def chunk_pipeline(
    filename,
    binary=None,
    from_page=0,
    to_page=100000,
    lang="Chinese",
    callback=None,
    vision_model=None,
    **kwargs,
):
    ctx = build_chunk_context(
        filename=filename,
        binary=binary,
        from_page=from_page,
        to_page=to_page,
        lang=lang,
        callback=callback,
        vision_model=vision_model,
        **kwargs,
    )
    pipeline = FileTypeRouter().route(filename)
    return pipeline.run(ctx)


__all__ = ["chunk_pipeline"]
