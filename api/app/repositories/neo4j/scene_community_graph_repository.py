from typing import Any, Protocol

from app.services.scene_community_graph_assembler import COMMUNITY_FIELDS, SCENE_FIELDS
from app.services.scene_community_graph_types import AuthorizedUser


class Connector(Protocol):
    async def execute_read_transaction(self, callback, **kwargs: Any): ...


def _query(label: str, variable: str, fields: tuple[str, ...]) -> str:
    # label/fields only come from constants, never from a request parameter.
    projection = ", ".join(f".{field}" for field in fields)
    return (
        f"MATCH ({variable}:{label} {{end_user_id: $end_user_id}}) "
        f"RETURN {variable}.id AS id, {variable} {{{projection}}} AS properties "
        "LIMIT $probe_limit"
    )


COMMUNITIES_QUERY = _query("SceneCommunity", "c", COMMUNITY_FIELDS)
SCENES_QUERY = _query("SceneSummary", "s", SCENE_FIELDS)


class Neo4jRepository:
    def __init__(self, connector: Connector, transaction_timeout: float = 3.0):
        self.connector = connector
        self.transaction_timeout = transaction_timeout

    async def _read(self, query: str, user: AuthorizedUser, limit: int) -> list[dict]:
        from neo4j import unit_of_work

        @unit_of_work(timeout=self.transaction_timeout)
        async def read(tx):
            result = await tx.run(query, end_user_id=str(user.end_user_id), probe_limit=limit)
            return [record.data() async for record in result]

        # The existing connector creates a separate read Session per call.
        return await self.connector.execute_read_transaction(read)

    async def communities(self, user: AuthorizedUser, limit: int) -> list[dict]:
        return await self._read(COMMUNITIES_QUERY, user, limit)

    async def scenes(self, user: AuthorizedUser, limit: int) -> list[dict]:
        return await self._read(SCENES_QUERY, user, limit)


class SharedSceneGraphConnector:
    """Use the existing shared Neo4j driver for bounded read transactions."""

    async def execute_read_transaction(self, callback, **kwargs):
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        return await Neo4jConnector(shared_driver=True).execute_read_transaction(callback, **kwargs)
