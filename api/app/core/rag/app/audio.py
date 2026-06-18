def chunk(filename, binary, lang, callback=None, seq2txt_mdl=None, **kwargs):
    from app.core.rag.chunk import chunk_pipeline

    return chunk_pipeline(
        filename,
        binary=binary,
        lang=lang,
        callback=callback,
        vision_model=seq2txt_mdl,
        **kwargs,
    )
