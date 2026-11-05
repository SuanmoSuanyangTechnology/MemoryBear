"""Community-oriented episodic page orchestration."""
import asyncio
import logging
from contextlib import asynccontextmanager
from app.core.config import settings
from app.core.memory.storage.enums import BackendType
from app.core.utils.datetime_utils import to_timestamp_ms, convert_neo4j_datetime_to_python
from app.core.memory.storage.custom.scene_community_storage import SceneCommunityStorage
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage.provider.neo4j.client import Neo4jClient

logger = logging.getLogger(__name__)

class MemoryEpisodicService:
    def __init__(self, storage=None):
        self.storage = storage

    @asynccontextmanager
    async def _storage(self):
        if self.storage is not None:
            yield self.storage
            return
        elastic_client = None
        neo4j_client = None
        read_backend = BackendType(settings.MEMORY_READ_BACKEND)
        try:
            if read_backend == BackendType.ELASTIC:
                elastic_client = ElasticClient()
                elastic_client.client = await elastic_client.connect()
            neo4j_client = Neo4jClient()
            neo4j_client.client = await neo4j_client.connect()
            yield SceneCommunityStorage(elastic_client, neo4j_client)
        finally:
            for backend_name, client in (
                ("Elasticsearch", elastic_client),
                ("Neo4j", neo4j_client),
            ):
                if client is None:
                    continue
                try:
                    await client.close()
                except Exception:
                    logger.exception("情景记忆客户端关闭失败: backend=%s", backend_name)

    async def get_scene_community_count(self, end_user_id: str) -> int:
        async with self._storage() as storage:
            return await storage.count([end_user_id])

    def _community(self, row):
        category = row.get('category_l1')
        if not isinstance(category, str) or not category.strip():
            raise RuntimeError('SceneCommunity.category_l1 is missing')
        result = dict(id=row['id'], topic_name=row.get('topic_name'),
                    topic_scope=row.get('topic_scope'), category_l1=category,
                    scene_count=row['scene_count'])
        for field in ('created_at', 'updated_at'):
            value = row.get(field)
            result[field] = (value if isinstance(value, int) else
                             to_timestamp_ms(convert_neo4j_datetime_to_python(value)))
        return result

    async def get_episodic_memory_overview(self, request, end_user_id, language='zh'):
        async with asyncio.timeout(5):
            async with self._storage() as storage:
                result = await storage.overview(end_user_id, request.category_l1,
                    request.keyword or '', request.sort, request.page, request.pagesize)
            result['items'] = [self._community(row) for row in result['items']]
            return result

    async def get_episodic_memory_details(self, request, end_user_id, language='zh'):
        async with asyncio.timeout(5):
            async with self._storage() as storage:
                row = await storage.details(
                    end_user_id, request.scene_community_id, request.page, request.pagesize,
                )
            result = self._community(row)
            result['summary'] = row.get('summary') or ''
            result['page'] = row['page']
            result['items'] = [dict(id=scene['id'], topic_scope=scene.get('topic_scope'),
                                     content=scene.get('content') or '')
                                for scene in row['items']]
            return result


memory_episodic_service = MemoryEpisodicService()
