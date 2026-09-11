from app.core.memory.storage.models import NodeSort


def compile_elasticsearch_sort(
    node_sort: NodeSort | None,
) -> list[dict[str, str]]:
    if node_sort is None:
        return []

    return [
        {sort_field.field: sort_field.direction.value.lower()}
        for sort_field in node_sort.fields
    ]
