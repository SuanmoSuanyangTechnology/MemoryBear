"""Neo4j persistence for Coding Agent Preference nodes."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.memory.models.graph_models import PreferenceNode
from app.core.memory.storage_services.preference_engine.models import PreferenceItem
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


class PreferenceRepository:
    def __init__(self, connector: Neo4jConnector):
        self.connector = connector

    async def get(
        self,
        *,
        end_user_id: str,
        domain: str,
        subject: str,
        situation_key: str,
    ) -> PreferenceNode | None:
        rows = await self.connector.execute_query(
            """
            MATCH (n:Preference {
                end_user_id: $end_user_id,
                domain: $domain,
                subject: $subject,
                situation_key: $situation_key
            })
            RETURN properties(n) AS node
            """,
            end_user_id=end_user_id,
            domain=domain,
            subject=subject,
            situation_key=situation_key,
        )
        return PreferenceNode.model_validate(rows[0]["node"]) if rows else None

    async def create_if_absent(
        self,
        *,
        end_user_id: str,
        domain: str,
        subject: str,
        situation_key: str,
        items: list[PreferenceItem],
    ) -> tuple[PreferenceNode, bool]:
        marker = uuid.uuid4().hex
        preference_id = str(uuid.uuid4())
        texts = [item.preference_text for item in items]
        modes = [item.mode for item in items]

        async def create_tx(tx, **_: Any):
            result = await tx.run(
                """
                MERGE (n:Preference {
                    end_user_id: $end_user_id,
                    domain: $domain,
                    subject: $subject,
                    situation_key: $situation_key
                })
                ON CREATE SET
                    n.preference_id = $preference_id,
                    n.mode = $modes,
                    n.preference_text = $texts,
                    n.preference_text_all = $text_all,
                    n.status = 'active',
                    n.created_at = datetime(),
                    n.updated_at = datetime(),
                    n._creation_marker = $marker
                WITH n, n._creation_marker = $marker AS created
                REMOVE n._creation_marker
                RETURN properties(n) AS node, created
                """,
                end_user_id=end_user_id,
                domain=domain,
                subject=subject,
                situation_key=situation_key,
                preference_id=preference_id,
                modes=modes,
                texts=texts,
                text_all=self.build_text_all(texts),
                marker=marker,
            )
            record = await result.single(strict=True)
            return record.data()

        row = await self.connector.execute_write_transaction(create_tx)
        return PreferenceNode.model_validate(row["node"]), bool(row["created"])

    async def update(
        self,
        node: PreferenceNode,
        items: list[PreferenceItem],
    ) -> PreferenceNode | None:
        texts = [item.preference_text for item in items]
        modes = [item.mode for item in items]

        async def update_tx(tx, **_: Any):
            result = await tx.run(
                """
                MATCH (n:Preference {
                    end_user_id: $end_user_id,
                    domain: $domain,
                    subject: $subject,
                    situation_key: $situation_key
                })
                SET n.mode = $modes,
                    n.preference_text = $texts,
                    n.preference_text_all = $text_all,
                    n.updated_at = datetime()
                RETURN properties(n) AS node
                """,
                end_user_id=node.end_user_id,
                domain=node.domain,
                subject=node.subject,
                situation_key=node.situation_key,
                modes=modes,
                texts=texts,
                text_all=self.build_text_all(texts),
            )
            record = await result.single()
            return record.data() if record else None

        row = await self.connector.execute_write_transaction(update_tx)
        return PreferenceNode.model_validate(row["node"]) if row else None

    @staticmethod
    def build_text_all(texts: list[str]) -> str | None:
        return f"{','.join(texts)}." if texts else None
