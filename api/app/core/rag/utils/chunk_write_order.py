from app.core.rag.models.chunk import DocumentChunk


NON_VECTORIZED_CHUNK_TYPES = {"source", "parent"}


def chunk_requires_vector(chunk: DocumentChunk) -> bool:
    chunk_type = (chunk.metadata or {}).get("chunk_type", "chunk")
    return chunk_type not in NON_VECTORIZED_CHUNK_TYPES


def prioritize_vectorized_chunks(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
    vectorized_chunks: list[DocumentChunk] = []
    non_vectorized_chunks: list[DocumentChunk] = []

    for chunk in chunks:
        if chunk_requires_vector(chunk):
            vectorized_chunks.append(chunk)
        else:
            non_vectorized_chunks.append(chunk)

    return vectorized_chunks + non_vectorized_chunks


def pop_vectorized_bootstrap_batch(
    batches: list[list[DocumentChunk]],
) -> tuple[int | None, list[DocumentChunk] | None]:
    for idx, batch in enumerate(batches):
        if any(chunk_requires_vector(chunk) for chunk in batch):
            return idx, batches.pop(idx)

    return None, None
