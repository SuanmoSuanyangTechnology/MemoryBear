# 中文翻译文件

此目录包含中文（简体）的翻译文件。

## 文件结构

- `common.json` - 通用翻译（成功消息、操作、验证）
- `auth.json` - 认证模块翻译
- `workspace.json` - 工作空间模块翻译
- `tenant.json` - 租户模块翻译
- `errors.json` - 错误消息翻译
- `enums.json` - 枚举值翻译

## 翻译文件格式

所有翻译文件使用 JSON 格式，支持嵌套结构。

示例：
```json
{
  "success": {
    "created": "创建成功",
    "updated": "更新成功"
  }
}
```
