# memory-storage-schema

Memory 存储 schema 的唯一事实来源（纯 Python，无第三方运行时依赖）：

- `memory_storage_schema.labels`：`MemoryNodeLabel` / `MemoryNodeType` 节点标签。
- `memory_storage_schema.definitions`：Elasticsearch 索引定义注册表
  （`INDEX_DEFINITIONS`、`IndexDefinition`、`get_index_name`、
  `get_embedding_field_name`、`validate_definition_registry` 等）。
- `memory_storage_schema.serialization`：文档归一化
  （`normalize_elasticsearch_document`、`route_embedding_field`、
  `normalize_document`）。

API 与 memory-jobs 通过本地路径依赖安装本包；API 内原有的
`app.core.memory.storage.enums` / `...elasticsearch.index.definitions` /
`...elasticsearch.serialization` 模块保留为薄 re-export shim，导入路径不变。

## 使用

```python
from memory_storage_schema import MemoryNodeType, get_index_name, normalize_document

normalize_document(MemoryNodeType.STATEMENT, {"created_at": None})
```

## 测试

```powershell
$env:PYTHONPATH = "..\..\packages\memory-storage-schema\src"
python -m pytest tests -q
```
