import logging

from app.core.rag.common.token_utils import num_tokens_from_string
from app.core.rag.nlp import concat_img


FULL_DOC_MAX_CHARS = 10000


def truncate_to_chars(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars]


def chunk_parent_child_pipeline(
    filename,
    binary=None,
    from_page=0,
    to_page=100000,
    lang="Chinese",
    callback=None,
    vision_model=None,
    **kwargs,
):
    parser_config = kwargs.get("parser_config", {})
    child_token_num = int(parser_config.get("chunk_token_num", 128))
    parent_token_num = int(parser_config.get("parent_chunk_token_num", 1024))

    if parent_token_num <= child_token_num:
        logging.warning(
            f"parent_chunk_token_num({parent_token_num}) <= chunk_token_num({child_token_num}), "
            f"falling back to default 1024"
        )
        parent_token_num = 1024

    from app.core.rag.chunk import chunk_pipeline

    child_res = chunk_pipeline(
        filename,
        binary=binary,
        from_page=from_page,
        to_page=to_page,
        lang=lang,
        callback=callback,
        vision_model=vision_model,
        **kwargs,
    )
    logging.info(f"[ParentChild] child: token_num={child_token_num}, chunk_count={len(child_res)}")

    parent_chunk_mode = parser_config.get("parent_chunk_mode", "paragraph")

    if parent_chunk_mode == "full-doc":
        all_texts = [child["content_with_weight"] for child in child_res]
        full_text = "\n\n".join(all_texts)
        truncated = truncate_to_chars(full_text, FULL_DOC_MAX_CHARS)
        parent_res = [{"content_with_weight": truncated, "image": None}]
        parent_id_map = {index: 0 for index in range(len(child_res))}
        logging.info(f"[ParentChild] parent: mode=full-doc, max_chars={FULL_DOC_MAX_CHARS}, chunk_count=1")
        return child_res, parent_res, parent_id_map

    parent_res: list[dict] = []
    parent_id_map: dict[int, int] = {}
    buffer_texts: list[str] = []
    buffer_images: list = []
    buffer_tokens = 0

    def flush_parent():
        nonlocal buffer_texts, buffer_images, buffer_tokens
        merged = "\n\n".join(buffer_texts)
        merged_image = None
        for image in buffer_images:
            merged_image = concat_img(merged_image, image) if merged_image else image
        parent_res.append({
            "content_with_weight": merged,
            "image": merged_image,
        })
        buffer_texts = []
        buffer_images = []
        buffer_tokens = 0

    for child_index, child in enumerate(child_res):
        text = child["content_with_weight"]
        image = child.get("image")
        token_count = num_tokens_from_string(text)

        if buffer_texts and buffer_tokens + token_count > parent_token_num:
            flush_parent()

        buffer_texts.append(text)
        if image is not None:
            buffer_images.append(image)
        buffer_tokens += token_count
        parent_id_map[child_index] = len(parent_res)

    if buffer_texts:
        flush_parent()

    logging.info(f"[ParentChild] parent: mode=paragraph, token_num={parent_token_num}, chunk_count={len(parent_res)}")
    return child_res, parent_res, parent_id_map
