"""Neo4j persistence and Outbox publication for Preference nodes."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from app.core.memory.models.graph_models import PreferenceNode
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.outbox.producer import enqueue_events
from app.core.memory.storage.outbox.types import OutboxEventInput
from app.core.memory.storage.provider.neo4j.client import _to_native
from app.core.utils.datetime_utils import utcnow_naive
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

if TYPE_CHECKING:
    from app.core.memory.storage_services.preference_engine.models import PreferenceItem


async def get_preference(
    connector: Neo4jConnector,
    *,
    end_user_id: str,
    domain: str,
    subject: str,
    situation_key: str,
) -> PreferenceNode | None:
    rows = await connector.execute_query(
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
    return PreferenceNode.model_validate(_to_native(rows[0]["node"])) if rows else None


async def create_preference_if_absent(
    connector: Neo4jConnector,
    *,
    end_user_id: str,
    domain: str,
    subject: str,
    situation_key: str,
    items: list[PreferenceItem],
) -> tuple[PreferenceNode, bool]:
    marker = uuid.uuid4().hex
    node_id = str(uuid.uuid4())
    texts = [item.preference_text for item in items]
    modes = [item.mode for item in items]

    async def create_tx(tx: Any) -> dict[str, Any]:
        now = utcnow_naive()
        result = await tx.run(
            """
            MERGE (n:Preference {
                end_user_id: $end_user_id,
                domain: $domain,
                subject: $subject,
                situation_key: $situation_key
            })
            ON CREATE SET
                n.id = $node_id,
                n.mode = $modes,
                n.preference_text = $texts,
                n.preference_text_all = $text_all,
                n.status = 'active',
                n.created_at = $created_at,
                n.updated_at = $updated_at,
                n._creation_marker = $marker
            WITH n, n._creation_marker = $marker AS created
            REMOVE n._creation_marker
            RETURN properties(n) AS node, created
            """,
            end_user_id=end_user_id,
            domain=domain,
            subject=subject,
            situation_key=situation_key,
            node_id=node_id,
            modes=modes,
            texts=texts,
            text_all=f"{','.join(texts)}." if texts else None,
            marker=marker,
            created_at=now,
            updated_at=now,
        )
        record = await result.single(strict=True)
        return record.data()

    row = await connector.execute_write_transaction(create_tx)
    node = PreferenceNode.model_validate(_to_native(row["node"]))
    created = bool(row["created"])
    if created:
        await enqueue_events(
            [
                OutboxEventInput(
                    label=MemoryNodeType.PREFERENCE,
                    node_id=node.id,
                )
            ]
        )
    return node, created


async def update_preference(
    connector: Neo4jConnector,
    node: PreferenceNode,
    items: list[PreferenceItem],
) -> PreferenceNode | None:
    texts = [item.preference_text for item in items]
    modes = [item.mode for item in items]

    async def update_tx(tx: Any) -> dict[str, Any] | None:
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
                n.updated_at = $updated_at
            RETURN properties(n) AS node
            """,
            end_user_id=node.end_user_id,
            domain=node.domain,
            subject=node.subject,
            situation_key=node.situation_key,
            modes=modes,
            texts=texts,
            text_all=f"{','.join(texts)}." if texts else None,
            updated_at=utcnow_naive(),
        )
        record = await result.single()
        return record.data() if record else None

    row = await connector.execute_write_transaction(update_tx)
    if row is None:
        return None
    saved = PreferenceNode.model_validate(_to_native(row["node"]))
    await enqueue_events(
        [
            OutboxEventInput(
                label=MemoryNodeType.PREFERENCE,
                node_id=saved.id,
            )
        ]
    )
    return saved
