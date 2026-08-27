from typing import Any

import pytest
from pydantic import ValidationError

from app.core.memory.storage.enums import (
    BackendType,
    MemoryNodeType,
    MemoryRelationshipType,
)
from app.core.memory.storage.models import (
    FilterCondition,
    FilterLogic,
    FilterOperator,
    NodeFilter,
    NodeProjection,
    NodeSort,
    SortDirection,
    SortField,
)
from app.core.memory.storage.provider.elasticsearch.compiler.filter_compiler import (
    compile_elasticsearch_filter,
)
from app.core.memory.storage.provider.elasticsearch.compiler.projection_compiler import (
    compile_elasticsearch_projection,
)
from app.core.memory.storage.provider.elasticsearch.compiler.sort_compiler import (
    compile_elasticsearch_sort,
)
from app.core.memory.storage.provider.neo4j.client import Neo4jClient, _to_native
from app.core.memory.storage.provider.neo4j.compiler.filter_compiler import compile_neo4j_filter
from app.core.memory.storage.provider.neo4j.compiler.projection_compiler import (
    compile_neo4j_projection,
)
from app.core.memory.storage.provider.neo4j.compiler.sort_compiler import compile_neo4j_sort
from app.core.memory.storage.tests.enums import TestMemoryNodeType



@pytest.mark.parametrize(
    ("operator", "value"),
    [
        (FilterOperator.IN, "not-a-collection"),
        (FilterOperator.NOT_IN, []),
        (FilterOperator.GT, None),
        (FilterOperator.EXISTS, "true"),
    ],
)
def test_filter_condition_rejects_non_portable_values(
    operator: FilterOperator,
    value: Any,
) -> None:
    with pytest.raises(ValidationError):
        FilterCondition(field="status", operator=operator, value=value)


def test_neo4j_filter_is_fully_parameterized() -> None:
    unsafe_field = "status`) MATCH (x) //"
    node_filter = NodeFilter(
        conditions=(
            FilterCondition(field=unsafe_field, value="active"),
            FilterCondition(field="score", operator=FilterOperator.GTE, value=10),
        )
    )

    predicate, parameters = compile_neo4j_filter(node_filter)

    assert predicate == (
        "(n[$filter_0_field] = $filter_0_value) AND "
        "(n[$filter_1_field] >= $filter_1_value)"
    )
    assert unsafe_field not in predicate
    assert parameters == {
        "filter_0_field": unsafe_field,
        "filter_0_value": "active",
        "filter_1_field": "score",
        "filter_1_value": 10,
    }


def test_neo4j_filter_supports_or_in_and_missing_checks() -> None:
    node_filter = NodeFilter(
        logic=FilterLogic.OR,
        conditions=(
            FilterCondition(
                field="status",
                operator=FilterOperator.IN,
                value={"active", "pending"},
            ),
            FilterCondition(
                field="deleted_at",
                operator=FilterOperator.EXISTS,
                value=False,
            ),
        ),
    )

    predicate, parameters = compile_neo4j_filter(node_filter)

    assert " OR " in predicate
    assert " IN $filter_0_value" in predicate
    assert "n[$filter_1_field] IS NULL" in predicate
    assert set(parameters["filter_0_value"]) == {"active", "pending"}


def test_elasticsearch_filter_uses_the_same_model() -> None:
    node_filter = NodeFilter(
        conditions=(
            FilterCondition(field="status", value="active"),
            FilterCondition(
                field="category",
                operator=FilterOperator.NOT_IN,
                value=["ignored", "deleted"],
            ),
        )
    )

    assert compile_elasticsearch_filter(node_filter) == {
        "bool": {
            "filter": [
                {"term": {"status": "active"}},
                {
                    "bool": {
                        "filter": {"exists": {"field": "category"}},
                        "must_not": {
                            "terms": {"category": ["ignored", "deleted"]}
                        },
                    }
                },
            ]
        }
    }


class _FakeResult:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self._data = data
        self.consumed = False

    async def data(self) -> list[dict[str, Any]]:
        return self._data

    async def consume(self) -> None:
        self.consumed = True


class _FakeSession:
    def __init__(
            self,
            calls: list[tuple[str, dict[str, Any]]],
            results: list[_FakeResult],
    ) -> None:
        self.calls = calls
        self.results = results

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def run(self, query: str, **parameters: Any) -> _FakeResult:
        self.calls.append((query, parameters))
        if "RETURN count(n) AS deleted" in query:
            data = [{"deleted": 2}]
        elif "RETURN r" in query:
            data = [{"r": parameters["properties"]}]
        elif "$properties" in query and "RETURN n" in query:
            data = [{"n": parameters["properties"]}]
        elif " AS n" in query:
            data = [{"n": {"id": 1}}]
        else:
            data = []
        result = _FakeResult(data)
        self.results.append(result)
        return result


class _FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: list[_FakeResult] = []

    def session(self) -> _FakeSession:
        return _FakeSession(self.calls, self.results)


async def test_neo4j_health_consumes_result() -> None:
    driver = _FakeDriver()
    client = Neo4jClient()
    client.client = driver  # type: ignore[assignment]

    await client.health()

    assert driver.calls == [("return 1", {})]
    assert driver.results[0].consumed is True


async def test_neo4j_embedding_search_zero_vector_returns_empty_dto() -> None:
    driver = _FakeDriver()
    client = Neo4jClient()
    client.client = driver  # type: ignore[assignment]

    result = await client.search_by_embedding(
        MemoryNodeType.STATEMENT,
        NodeFilter.eq("id", "node-1"),
        [0.0, 0.0],
        1,
    )

    assert result.backend == BackendType.NEO4J
    assert result.items == []
    assert result.total == 0
    assert driver.calls == []


async def test_neo4j_fulltext_search_blank_text_returns_empty_dto() -> None:
    driver = _FakeDriver()
    client = Neo4jClient()
    client.client = driver  # type: ignore[assignment]

    result = await client.search_by_fulltext(
        MemoryNodeType.STATEMENT,
        NodeFilter.eq("id", "node-1"),
        "   ",
        1,
    )

    assert result.backend == BackendType.NEO4J
    assert result.items == []
    assert result.total == 0
    assert driver.calls == []


def test_neo4j_to_native_converts_neo4j_values_to_python() -> None:
    import datetime as _datetime

    from neo4j.graph import Node
    from neo4j.time import DateTime

    created_at = DateTime(2026, 8, 19, 10, 30, 0)
    node = Node(
        None,
        "element-1",
        1,
        ["Test"],
        {
            "id": 1,
            "created_at": created_at,
            "items": [{"ts": created_at}],
        },
    )

    result = _to_native(node)

    assert result == {
        "id": 1,
        "created_at": _datetime.datetime(2026, 8, 19, 10, 30, 0),
        "items": [{"ts": _datetime.datetime(2026, 8, 19, 10, 30, 0)}],
    }
    assert type(result) is dict
    assert type(result["created_at"]) is _datetime.datetime
    assert type(result["items"][0]["ts"]) is _datetime.datetime


def test_neo4j_to_native_passes_through_plain_python_values() -> None:
    assert _to_native(None) is None
    assert _to_native("text") == "text"
    assert _to_native(42) == 42
    assert _to_native([1, "a", None]) == [1, "a", None]


async def test_neo4j_save_relationship_merges_by_id_and_sets_properties() -> None:
    driver = _FakeDriver()
    client = Neo4jClient()
    client.client = driver  # type: ignore[assignment]
    data = {"id": "edge-1", "weight": 0.8}

    result = await client.save_relationship(
        relationship_type=MemoryRelationshipType.RELATES_TO,
        source="node-1",
        target="node-2",
        data=data,
    )

    query, parameters = driver.calls[0]
    assert "MATCH (source {id: $source})" in query
    assert "MATCH (target {id: $target})" in query
    assert "MERGE (source)-[r:`RELATES_TO` {id: $id}]->(target)" in query
    assert "SET r = $properties" in query
    assert parameters == {
        "id": "edge-1",
        "source": "node-1",
        "target": "node-2",
        "properties": {"id": "edge-1", "weight": 0.8},
    }
    assert data == {"id": "edge-1", "weight": 0.8}
    assert result.backend == BackendType.NEO4J
    assert result.affected_count == 1
    assert result.ids == ["edge-1"]
    assert result.data == [data]


def test_memory_relationship_type_covers_physical_graph_relationships() -> None:
    assert {item.value for item in MemoryRelationshipType} == {
        "BELONGS_TO_COMMUNITY",
        "BELONGS_TO_CONVERSATION",
        "BELONGS_TO_DIALOG",
        "CONTAINS",
        "DERIVED_FROM",
        "DERIVED_FROM_STATEMENT",
        "EXTRACTED_RELATIONSHIP",
        "HAS_ORIGINAL_CONTENT",
        "HAS_PERCEPTUAL",
        "MENTIONS",
        "PRUNED_TO",
        "REFERENCES_ENTITY",
        "RELATES_TO",
        "STATEMENT_ENTITY",
    }


async def test_neo4j_save_relationship_requires_relationship_id() -> None:
    client = Neo4jClient()

    with pytest.raises(ValueError, match="Relationship id field is required"):
        await client.save_relationship(
            MemoryRelationshipType.RELATES_TO,
            "node-1",
            "node-2",
            {},
        )


@pytest.mark.parametrize("relationship_type", ["RELATES_TO", "", None, 1])
async def test_neo4j_save_relationship_requires_relationship_type_enum(
    relationship_type: Any,
) -> None:
    client = Neo4jClient()

    with pytest.raises(KeyError, match="relationship type.*not supported"):
        await client.save_relationship(
            relationship_type,
            "node-1",
            "node-2",
            {"id": "edge-1"},
        )


async def test_neo4j_update_node_uses_custom_filter_without_data_id() -> None:
    driver = _FakeDriver()
    client = Neo4jClient()
    client.client = driver  # type: ignore[assignment]
    node_filter = NodeFilter.eq("external_id", "node-1")

    result = await client.update_node(
        TestMemoryNodeType.TEST,
        {"change": True},
        node_filter=node_filter,
    )

    query, parameters = driver.calls[0]
    assert "MATCH (n:Test)" in query
    assert "WHERE (n[$filter_0_field] = $filter_0_value)" in query
    assert "MERGE" not in query
    assert parameters == {
        "properties": {"change": True},
        "filter_0_field": "external_id",
        "filter_0_value": "node-1",
    }
    assert result.backend == BackendType.NEO4J
    assert result.affected_count == 1
    assert result.ids == []
    assert result.data == [{"change": True}]



def test_neo4j_filter_supports_nested_boolean_groups() -> None:
    node_filter = NodeFilter.all_of(
        FilterCondition(field="tenant_id", value="tenant-1"),
        NodeFilter.any_of(
            FilterCondition(field="status", value="pending"),
            FilterCondition(
                field="priority",
                operator=FilterOperator.IN,
                value=["high", "urgent"],
            ),
        ),
    )

    predicate, parameters = compile_neo4j_filter(node_filter)

    assert predicate == (
        "(n[$filter_0_field] = $filter_0_value) AND "
        "((n[$filter_1_0_field] = $filter_1_0_value) OR "
        "(n[$filter_1_1_field] IN $filter_1_1_value))"
    )
    assert parameters == {
        "filter_0_field": "tenant_id",
        "filter_0_value": "tenant-1",
        "filter_1_0_field": "status",
        "filter_1_0_value": "pending",
        "filter_1_1_field": "priority",
        "filter_1_1_value": ["high", "urgent"],
    }


def test_elasticsearch_filter_supports_nested_boolean_groups() -> None:
    node_filter = NodeFilter.all_of(
        FilterCondition(field="tenant_id", value="tenant-1"),
        NodeFilter.any_of(
            FilterCondition(field="status", value="pending"),
            FilterCondition(field="status", value="running"),
        ),
    )

    assert compile_elasticsearch_filter(node_filter) == {
        "bool": {
            "filter": [
                {"term": {"tenant_id": "tenant-1"}},
                {
                    "bool": {
                        "should": [
                            {"term": {"status": "pending"}},
                            {"term": {"status": "running"}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
            ]
        }
    }


def test_neo4j_filter_uses_cypher_not_in_syntax() -> None:
    node_filter = NodeFilter(
        conditions=(
            FilterCondition(
                field="status",
                operator=FilterOperator.NOT_IN,
                value=["deleted", "ignored"],
            ),
        )
    )

    predicate, parameters = compile_neo4j_filter(node_filter)

    assert predicate == "(NOT n[$filter_0_field] IN $filter_0_value)"
    assert parameters == {
        "filter_0_field": "status",
        "filter_0_value": ["deleted", "ignored"],
    }


@pytest.mark.parametrize(
    "fields",
    [
        (),
        ("id", " "),
        ("id", "id"),
    ],
)
def test_node_projection_rejects_invalid_fields(fields: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        NodeProjection(fields=fields)


def test_neo4j_projection_defaults_to_full_node() -> None:
    assert compile_neo4j_projection(None) == ("n", {})


def test_neo4j_projection_compiles_selected_fields_and_escapes_identifiers() -> None:
    projection = NodeProjection.of("id", "display name", "unsafe`} RETURN 1 //")

    return_expression, parameters = compile_neo4j_projection(projection)

    assert return_expression == (
        "n { .`id`, .`display name`, .`unsafe``} RETURN 1 //` } AS n"
    )
    assert parameters == {}


async def test_neo4j_get_node_uses_selected_field_projection() -> None:
    driver = _FakeDriver()
    client = Neo4jClient()
    client.client = driver  # type: ignore[assignment]

    await client.get_node(
        TestMemoryNodeType.TEST,
        NodeFilter.eq("id", 1),
        projection=NodeProjection.of("id", "name"),
    )

    query, parameters = driver.calls[0]
    assert "WHERE (n[$filter_0_field] = $filter_0_value)" in query
    assert "RETURN n { .`id`, .`name` } AS n" in query
    assert parameters == {
        "filter_0_field": "id",
        "filter_0_value": 1,
    }


def test_node_sort_rejects_empty_blank_and_duplicate_fields() -> None:
    with pytest.raises(ValidationError):
        NodeSort.asc()
    with pytest.raises(ValidationError):
        SortField(field=" ")
    with pytest.raises(ValidationError):
        NodeSort(
            fields=(
                SortField(field="score"),
                SortField(field="score", direction=SortDirection.DESC),
            )
        )


def test_node_sort_convenience_constructors_set_direction() -> None:
    ascending = NodeSort.asc("created_at", "id")
    descending = NodeSort.desc("score")

    assert [field.direction for field in ascending.fields] == [
        SortDirection.ASC,
        SortDirection.ASC,
    ]
    assert descending.fields[0].direction == SortDirection.DESC


def test_neo4j_sort_defaults_to_no_ordering() -> None:
    assert compile_neo4j_sort(None) == ("", {})


def test_neo4j_sort_parameterizes_fields_and_whitelists_direction() -> None:
    unsafe_field = "score] DESC MATCH (x) //"
    node_sort = NodeSort(
        fields=(
            SortField(field=unsafe_field, direction=SortDirection.DESC),
            SortField(field="id", direction=SortDirection.ASC),
        )
    )

    order_by, parameters = compile_neo4j_sort(node_sort)

    assert order_by == (
        "ORDER BY n[$sort_0_field] DESC, n[$sort_1_field] ASC"
    )
    assert unsafe_field not in order_by
    assert parameters == {
        "sort_0_field": unsafe_field,
        "sort_1_field": "id",
    }


async def test_neo4j_get_node_sorts_before_projecting_fields() -> None:
    driver = _FakeDriver()
    client = Neo4jClient()
    client.client = driver  # type: ignore[assignment]

    await client.get_node(
        TestMemoryNodeType.TEST,
        NodeFilter.eq("category", "sort-test"),
        projection=NodeProjection.of("id"),
        node_sort=NodeSort(
            fields=(
                SortField(field="score", direction=SortDirection.DESC),
                SortField(field="id", direction=SortDirection.ASC),
            )
        ),
    )

    query, parameters = driver.calls[0]
    assert "WITH n" in query
    assert "ORDER BY n[$sort_0_field] DESC, n[$sort_1_field] ASC" in query
    assert "RETURN n { .`id` } AS n" in query
    assert query.index("ORDER BY") < query.index("RETURN")
    assert parameters == {
        "filter_0_field": "category",
        "filter_0_value": "sort-test",
        "sort_0_field": "score",
        "sort_1_field": "id",
    }


def test_elasticsearch_projection_defaults_to_full_source() -> None:
    assert compile_elasticsearch_projection(None) is None


def test_elasticsearch_projection_compiles_source_fields_in_order() -> None:
    projection = NodeProjection.of("id", "display name", "unsafe} field")

    assert compile_elasticsearch_projection(projection) == [
        "id",
        "display name",
        "unsafe} field",
    ]


def test_elasticsearch_sort_defaults_to_no_ordering() -> None:
    assert compile_elasticsearch_sort(None) == []


def test_elasticsearch_sort_preserves_field_order_and_maps_direction() -> None:
    unsafe_field = "score}]} malicious"
    node_sort = NodeSort(
        fields=(
            SortField(field=unsafe_field, direction=SortDirection.DESC),
            SortField(field="id", direction=SortDirection.ASC),
        )
    )

    assert compile_elasticsearch_sort(node_sort) == [
        {unsafe_field: "desc"},
        {"id": "asc"},
    ]


async def test_neo4j_delete_node_uses_parameterized_filter_and_returns_count() -> None:
    driver = _FakeDriver()
    client = Neo4jClient()
    client.client = driver  # type: ignore[assignment]
    unsafe_field = "external_id`) DETACH DELETE all //"
    node_filter = NodeFilter.all_of(
        FilterCondition(field=unsafe_field, value="node-1"),
        FilterCondition(field="tenant_id", value="tenant-1"),
    )

    result = await client.delete_node(
        TestMemoryNodeType.TEST,
        node_filter=node_filter,
    )

    query, parameters = driver.calls[0]
    assert "MATCH (n:Test)" in query
    assert (
        "WHERE (n[$filter_0_field] = $filter_0_value) AND "
        "(n[$filter_1_field] = $filter_1_value)"
    ) in query
    assert "DETACH DELETE n" in query
    assert "RETURN count(n) AS deleted" in query
    assert unsafe_field not in query
    assert parameters == {
        "filter_0_field": unsafe_field,
        "filter_0_value": "node-1",
        "filter_1_field": "tenant_id",
        "filter_1_value": "tenant-1",
    }
    assert result.backend == BackendType.NEO4J
    assert result.affected_count == 2


async def test_neo4j_delete_node_draft_sets_delete_at_without_detaching() -> None:
    driver = _FakeDriver()
    client = Neo4jClient()
    client.client = driver  # type: ignore[assignment]
    node_filter = NodeFilter.any_of(
        FilterCondition(field="external_id", value="node-1"),
        FilterCondition(field="external_id", value="node-2"),
    )

    result = await client.delete_node(
        TestMemoryNodeType.TEST,
        node_filter=node_filter,
        draft=True,
    )

    query, parameters = driver.calls[0]
    assert "MATCH (n:Test)" in query
    assert (
        "WHERE ((n[$filter_0_field] = $filter_0_value) OR "
        "(n[$filter_1_field] = $filter_1_value)) "
        "AND n.delete_at IS NULL"
    ) in query
    assert "SET n.delete_at = datetime()" in query
    assert "DETACH DELETE" not in query
    assert "RETURN count(n) AS deleted" in query
    assert parameters == {
        "filter_0_field": "external_id",
        "filter_0_value": "node-1",
        "filter_1_field": "external_id",
        "filter_1_value": "node-2",
    }
    assert result.backend == BackendType.NEO4J
    assert result.affected_count == 2


def test_node_projection_accepts_structured_fields_and_keeps_strings() -> None:
    from app.core.memory.storage.models import ProjectionField

    aliased = ProjectionField(field="name", alias="display_name")
    projection = NodeProjection.of("id", aliased)

    assert projection.fields == ("id", aliased)


@pytest.mark.parametrize(
    "field",
    [
        {"field": " "},
        {"field": "name", "alias": " "},
    ],
)
def test_projection_field_rejects_blank_names(field: dict[str, str]) -> None:
    from app.core.memory.storage.models import ProjectionField

    with pytest.raises(ValidationError):
        ProjectionField(**field)


def test_node_projection_rejects_duplicate_sources_and_output_names() -> None:
    from app.core.memory.storage.models import ProjectionField

    with pytest.raises(ValidationError):
        NodeProjection.of("name", ProjectionField(field="name", alias="label"))
    with pytest.raises(ValidationError):
        NodeProjection.of("id", ProjectionField(field="name", alias="id"))


def test_neo4j_projection_compiles_structured_aliases_and_escapes_them() -> None:
    from app.core.memory.storage.models import ProjectionField

    projection = NodeProjection.of(
        "id",
        ProjectionField(field="display name", alias="label"),
        ProjectionField(field="unsafe` field", alias="unsafe` alias"),
    )

    assert compile_neo4j_projection(projection) == (
        (
            "n { .`id`, `label`: n.`display name`, "
            "`unsafe`` alias`: n.`unsafe`` field` } AS n"
        ),
        {},
    )


def test_elasticsearch_projection_uses_sources_and_applies_aliases() -> None:
    from app.core.memory.storage.models import ProjectionField
    from app.core.memory.storage.provider.elasticsearch.compiler.projection_compiler import (
        apply_elasticsearch_projection,
    )

    projection = NodeProjection.of(
        "id",
        ProjectionField(field="name", alias="display_name"),
    )

    assert compile_elasticsearch_projection(projection) == ["id", "name"]
    assert apply_elasticsearch_projection(
        {"id": "node-1", "name": "Alice"},
        projection,
    ) == {"id": "node-1", "display_name": "Alice"}


def test_coalesce_projection_field_validates_fields_and_alias() -> None:
    from app.core.memory.storage.models import CoalesceProjectionField

    invalid_values = [
        {"fields": (), "alias": "display_name"},
        {"fields": ("name", " "), "alias": "display_name"},
        {"fields": ("name", "name"), "alias": "display_name"},
        {"fields": ("name",), "alias": " "},
    ]

    for value in invalid_values:
        with pytest.raises(ValidationError):
            CoalesceProjectionField(**value)


def test_node_projection_allows_coalesce_sources_to_overlap_direct_fields() -> None:
    from app.core.memory.storage.models import CoalesceProjectionField

    projection = NodeProjection.of(
        "nickname",
        CoalesceProjectionField(
            fields=("nickname", "name"),
            alias="display_name",
        ),
    )

    assert len(projection.fields) == 2


def test_node_projection_rejects_duplicate_coalesce_output_name() -> None:
    from app.core.memory.storage.models import CoalesceProjectionField

    with pytest.raises(ValidationError):
        NodeProjection.of(
            "display_name",
            CoalesceProjectionField(
                fields=("nickname", "name"),
                alias="display_name",
            ),
        )


def test_neo4j_projection_compiles_parameterized_coalesce() -> None:
    from app.core.memory.storage.models import CoalesceProjectionField

    projection = NodeProjection.of(
        "id",
        CoalesceProjectionField(
            fields=("nickname", "unsafe` name"),
            alias="display` name",
            default="Unknown",
        ),
    )

    expression, parameters = compile_neo4j_projection(projection)

    assert expression == (
        "n { .`id`, `display`` name`: "
        "coalesce(n.`nickname`, n.`unsafe`` name`, $projection_1_default) } AS n"
    )
    assert parameters == {"projection_1_default": "Unknown"}


def test_neo4j_projection_coalesce_without_default_has_no_parameter() -> None:
    from app.core.memory.storage.models import CoalesceProjectionField

    expression, parameters = compile_neo4j_projection(
        NodeProjection.of(
            CoalesceProjectionField(
                fields=("nickname", "name"),
                alias="display_name",
            )
        )
    )

    assert expression == (
        "n { `display_name`: coalesce(n.`nickname`, n.`name`) } AS n"
    )
    assert parameters == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            {"id": "1", "nickname": "Al", "name": "Alice"},
            {"id": "1", "display_name": "Al"},
        ),
        (
            {"id": "1", "nickname": None, "name": "Alice"},
            {"id": "1", "display_name": "Alice"},
        ),
        (
            {"id": "1", "nickname": None, "name": None},
            {"id": "1", "display_name": "Unknown"},
        ),
        (
            {"id": "1"},
            {"id": "1", "display_name": "Unknown"},
        ),
    ],
)
def test_elasticsearch_projection_evaluates_coalesce(
    source: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    from app.core.memory.storage.models import CoalesceProjectionField
    from app.core.memory.storage.provider.elasticsearch.compiler.projection_compiler import (
        apply_elasticsearch_projection,
    )

    projection = NodeProjection.of(
        "id",
        CoalesceProjectionField(
            fields=("nickname", "name"),
            alias="display_name",
            default="Unknown",
        ),
    )

    assert compile_elasticsearch_projection(projection) == [
        "id",
        "nickname",
        "name",
    ]
    assert apply_elasticsearch_projection(source, projection) == expected



async def test_neo4j_get_node_passes_coalesce_default_as_parameter() -> None:
    from app.core.memory.storage.models import CoalesceProjectionField

    driver = _FakeDriver()
    client = Neo4jClient()
    client.client = driver  # type: ignore[assignment]

    await client.get_node(
        TestMemoryNodeType.TEST,
        NodeFilter.eq("id", 1),
        projection=NodeProjection.of(
            CoalesceProjectionField(
                fields=("nickname", "name"),
                alias="display_name",
                default="Unknown",
            )
        ),
    )

    query, parameters = driver.calls[0]
    assert (
        "RETURN n { `display_name`: "
        "coalesce(n.`nickname`, n.`name`, $projection_0_default) } AS n"
    ) in query
    assert parameters == {
        "filter_0_field": "id",
        "filter_0_value": 1,
        "projection_0_default": "Unknown",
    }



def test_elasticsearch_projection_supports_virtual_score_field() -> None:
    from app.core.memory.storage.models import ProjectionField
    from app.core.memory.storage.provider.elasticsearch.compiler.projection_compiler import (
        apply_elasticsearch_projection,
    )

    projection = NodeProjection.of(
        "id",
        ProjectionField(field="score", alias="similarity"),
    )

    assert compile_elasticsearch_projection(
        projection,
        virtual_fields={"score"},
    ) == ["id"]
    assert apply_elasticsearch_projection(
        {"id": "node-1"},
        projection,
        virtual_fields={"score": 0.75},
    ) == {"id": "node-1", "similarity": 0.75}
