# Memory Bear 前端项目

基于 React + TypeScript + Vite + Ant Design 构建的知识库管理系统前端应用。

## 技术栈

- **框架**: React 18 + TypeScript
- **构建工具**: Vite
- **UI 组件库**: Ant Design 5
- **样式**: Tailwind CSS 4
- **路由**: React Router 6
- **状态管理**: Zustand
- **国际化**: i18next
- **图表**: ECharts
- **其他**: React Markdown

## 环境要求

- Node.js >= 20.19+, 22.12+
- npm 或 yarn

## 安装

```bash
# 克隆项目
git clone <repository-url>

# 进入项目目录
cd memory-bear-font-end

# 安装依赖
npm install
```

## 运行

### 开发环境

```bash
npm run dev
```

启动后访问: `http://localhost:5173`

### 生产构建

```bash
npm run build
```

构建产物输出到 `dist` 目录。

### 预览构建结果

```bash
npm run preview
```

## 代码检查

```bash
npm run lint
```

## 项目结构

```
src/
├── api/              # API 接口
├── assets/           # 静态资源
├── components/       # 公共组件
├── hooks/            # 自定义 Hooks
├── i18n/             # 国际化配置
├── routes/           # 路由配置
├── store/            # 状态管理
├── styles/           # 全局样式
├── utils/            # 工具函数
├── views/            # 页面视图
├── App.tsx           # 应用入口组件
└── main.tsx          # 应用入口文件
```

## 配置说明

- 开发服务器默认监听 `0.0.0.0:5173`
- API 代理配置在 `vite.config.ts` 中
- 路径别名 `@` 指向 `src` 目录

## License

Private
