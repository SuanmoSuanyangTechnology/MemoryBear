import numpy as np


def compute_cosine_similarity(
        batch_vectors: list, query_vec
) -> list[float]:
    vecs = np.array(batch_vectors, dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    zero_norm_mask = norms[:, 0] == 0.0
    if zero_norm_mask.any():
        norms[zero_norm_mask, 0] = 1.0
    vecs = vecs / norms
    sims = np.clip(vecs @ query_vec, 0, 1)
    sims[zero_norm_mask] = 0.0
    return sims.tolist()
