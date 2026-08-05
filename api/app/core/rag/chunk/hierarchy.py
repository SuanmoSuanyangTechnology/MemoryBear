from typing import Any


class GroupedChildChunks(list[dict]):
    pass


def validate_parent_child_result(
    child_chunks: list[Any],
    parent_chunks: list[Any],
    parent_id_map: dict[int, int],
    mode: str = "parent_child",
) -> None:
    if not child_chunks and not parent_chunks and not parent_id_map:
        return

    expected_child_indices = set(range(len(child_chunks)))
    mapped_child_indices = set(parent_id_map)
    if mapped_child_indices != expected_child_indices:
        missing = sorted(expected_child_indices - mapped_child_indices)
        unexpected = sorted(mapped_child_indices - expected_child_indices, key=str)
        raise ValueError(
            f"Invalid {mode} hierarchy: child mapping is incomplete "
            f"(missing={missing}, unexpected={unexpected})."
        )

    referenced_parent_indices: set[int] = set()
    for child_index in range(len(child_chunks)):
        parent_index = parent_id_map[child_index]
        if not isinstance(parent_index, int) or not 0 <= parent_index < len(parent_chunks):
            raise ValueError(
                f"Invalid {mode} hierarchy: child index {child_index} maps to "
                f"invalid parent index {parent_index!r}."
            )
        referenced_parent_indices.add(parent_index)

    for parent_index in range(len(parent_chunks)):
        if parent_index not in referenced_parent_indices:
            raise ValueError(f"Invalid {mode} hierarchy: parent index {parent_index} has no children.")
