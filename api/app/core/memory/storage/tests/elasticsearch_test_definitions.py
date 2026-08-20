"""Elasticsearch index definitions owned exclusively by storage tests."""

from app.core.memory.storage.provider.elasticsearch.index.definitions import (
    INDEX_SHARD_COUNT,
    IndexDefinition,
)
from app.core.memory.storage.tests.enums import TestMemoryNodeType

TEST_INDEX_LABEL = TestMemoryNodeType.TEST
TEST_INDEX_DEFINITION = IndexDefinition(
    name="test",
    schema_version=1,
    generation=1,
    settings={"number_of_shards": INDEX_SHARD_COUNT},
    mappings={
        "dynamic_templates": [
            {
                "strings_as_keywords": {
                    "match_mapping_type": "string",
                    "mapping": {
                        "type": "keyword",
                        "ignore_above": 8191,
                    },
                }
            }
        ],
        "properties": {
            "embedding": {
                "type": "dense_vector",
                "dims": 1024,
                "index": True,
                "similarity": "cosine",
            }
        },
    },
)
