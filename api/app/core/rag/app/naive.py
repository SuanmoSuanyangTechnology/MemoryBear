def chunk(filename, binary=None, from_page=0, to_page=100000,
          lang="Chinese", callback=None, vision_model=None, **kwargs):
    from app.core.rag.chunk import chunk_pipeline

    return chunk_pipeline(
        filename=filename,
        binary=binary,
        from_page=from_page,
        to_page=to_page,
        lang=lang,
        callback=callback,
        vision_model=vision_model,
        **kwargs,
    )


def chunk_parent_child(
    filename, binary=None, from_page=0, to_page=100000,
    lang="Chinese", callback=None, vision_model=None, **kwargs
):
    from app.core.rag.chunk import chunk_parent_child_pipeline

    return chunk_parent_child_pipeline(
        filename=filename,
        binary=binary,
        from_page=from_page,
        to_page=to_page,
        lang=lang,
        callback=callback,
        vision_model=vision_model,
        **kwargs,
    )


def chunk_pipeline(filename, binary=None, from_page=0, to_page=100000,
                   lang="Chinese", callback=None, vision_model=None, **kwargs):
    from app.core.rag.chunk import chunk_pipeline as pipeline_entry

    return pipeline_entry(
        filename=filename,
        binary=binary,
        from_page=from_page,
        to_page=to_page,
        lang=lang,
        callback=callback,
        vision_model=vision_model,
        **kwargs,
    )


def chunk_parent_child_pipeline(
    filename, binary=None, from_page=0, to_page=100000,
    lang="Chinese", callback=None, vision_model=None, **kwargs
):
    from app.core.rag.chunk import chunk_parent_child_pipeline as pipeline_entry

    return pipeline_entry(
        filename=filename,
        binary=binary,
        from_page=from_page,
        to_page=to_page,
        lang=lang,
        callback=callback,
        vision_model=vision_model,
        **kwargs,
    )
