#!/bin/bash

echo "🔧 开始修复 @dnd-kit 依赖问题..."
echo ""

# 1. 删除旧的依赖
echo "📦 删除 node_modules..."
rm -rf node_modules

# 2. 删除 lock 文件
echo "🗑️  删除 lock 文件..."
rm -f package-lock.json yarn.lock pnpm-lock.yaml

# 3. 清除缓存
echo "🧹 清除 npm 缓存..."
npm cache clean --force

# 4. 重新安装
echo "⬇️  重新安装依赖..."
npm install

echo ""
echo "✅ 修复完成！"
echo ""
echo "💡 接下来的步骤："
echo "   1. 重启你的编辑器/IDE"
echo "   2. 如果使用 VS Code，按 Cmd+Shift+P，输入 'TypeScript: Restart TS Server'"
echo "   3. 运行 'npm run dev' 测试"
echo ""
