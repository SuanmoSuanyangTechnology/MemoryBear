# Contributing to MemoryBear

感谢你对 MemoryBear 的关注！我们欢迎任何形式的贡献。

## 如何贡献

### 报告问题

- 使用 [GitHub Issues](https://github.com/SuanmoSuanyangTechnology/MemoryBear/issues) 提交 Bug 报告或功能建议
- 提交前请先搜索是否已有相同的 Issue

### 提交代码

1. Fork 本仓库
2. 创建功能分支：`git checkout -b feature/your-feature-name`
3. 提交更改：遵循 [Conventional Commits](https://www.conventionalcommits.org/) 格式
4. 推送分支：`git push origin feature/your-feature-name`
5. 创建 Pull Request

### Commit 格式

```
<type>(<scope>): <description>

[optional body]
```

**Type 类型：**

| Type | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `style` | 代码格式（不影响逻辑） |
| `refactor` | 重构（非新功能、非修复） |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/工具链变更 |

**示例：**

```
feat(extraction): add ALIAS_OF relationship for entity deduplication
fix(search): correct hybrid search ranking when activation values are missing
docs(readme): update architecture diagram with generated images
```

### 开发环境

```bash
# 后端
cd api
pip install uv && uv sync
source .venv/bin/activate
pytest  # 运行测试

# 前端
cd web
npm install
npm run lint  # 代码检查
npm run dev   # 开发服务器
```

### 代码规范

- Python：遵循 PEP 8，行宽不超过 120 字符
- TypeScript：通过 ESLint 检查
- 提交前确保测试通过

## 行为准则

请保持友善和尊重。我们致力于为所有人提供一个开放、包容的社区环境。
