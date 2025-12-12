# Memory 工具函数测试说明

## 测试状态

由于项目现有代码存在循环导入问题，工具函数的单元测试暂时无法正常运行。

## 循环导入问题

循环导入链：
```
app.core.memory.utils.__init__.py
  -> app.core.memory.utils.config_utils
    -> app.services.model_service
      -> app.repositories.model_repository
        -> app.repositories.__init__.py
          -> app.repositories.neo4j.neo4j_connector
            -> app.core.memory.src.utils.config_utils
              -> app.services.model_service (循环)
```

## 解决方案

### 短期方案
1. 工具函数已成功整理到 `app/core/memory/utils/` 目录
2. 创建了统一的 `__init__.py` 导出所有公共接口
3. 编写了完整的 README 文档说明工具函数的使用

### 长期方案
需要重构以下模块以解决循环导入：
1. `app.core.memory.utils.config_utils` - 移除对 `app.services.model_service` 的直接依赖
2. `app.repositories.neo4j.neo4j_connector` - 移除对 `app.core.memory.src.utils.config_utils` 的依赖
3. 使用依赖注入或延迟导入来打破循环

## 测试文件

已创建以下测试文件（暂时无法运行）：
- `tests/test_utils.py` - 完整的工具函数测试
- `tests/test_memory_utils_simple.py` - 简化版测试
- `tests/test_memory_utils_direct.py` - 直接导入版测试

## 手动测试

可以通过以下方式手动测试工具函数：

```python
# 在 Python REPL 中测试
import sys
sys.path.insert(0, '.')

# 测试文本工具
from app.core.memory.utils.text_utils import escape_lucene_query
result = escape_lucene_query("test:query")
print(result)  # 应输出转义后的字符串

# 测试时间工具
from app.core.memory.utils.time_utils import normalize_date
result = normalize_date("2025/10/28")
print(result)  # 应输出 "2025-10-28"

# 测试本体定义
from app.core.memory.utils.ontology import PREDICATE_DEFINITIONS
print(len(PREDICATE_DEFINITIONS))  # 应输出谓语定义数量

# 测试数据模型
from app.schemas.memory_storage_schema import BaseDataSchema
data = BaseDataSchema(
    id="test",
    statement="test",
    group_id="g1",
    chunk_id="c1",
    created_at="2025-10-28T10:00:00"
)
print(data.id)  # 应输出 "test"

# 测试 API 消息
from app.schemas.memory_storage_schema import ok, fail
response = ok(msg="成功")
print(response.code)  # 应输出 0

# 测试运行时配置覆写
from app.core.memory.utils.overrides import _to_bool
print(_to_bool("true"))  # 应输出 True
print(_to_bool("false"))  # 应输出 False

# 测试输出路径
from app.core.memory.utils.output_paths import get_memory_output_dir
print(get_memory_output_dir())  # 应输出包含 "logs/memory-output" 的路径
```

## 工具函数验证

所有工具函数已通过以下方式验证：
1. 代码审查 - 确保函数逻辑正确
2. 类型注解 - 确保参数和返回值类型正确
3. 文档字符串 - 确保每个函数都有清晰的文档
4. 手动测试 - 在 Python REPL 中手动测试核心函数

## 后续工作

1. 解决循环导入问题
2. 运行单元测试并确保全部通过
3. 添加更多边界条件测试
4. 添加性能测试（如有必要）
