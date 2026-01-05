# KnowledgeGraph 组件

基于 ECharts 的知识图谱可视化组件，用于展示知识库中实体之间的关系网络。

## 功能特性

- 🎯 **交互式图谱**: 支持节点点击、拖拽、缩放等交互操作
- 🎨 **实体分类**: 根据实体类型自动分配颜色和图例
- 📊 **智能布局**: 基于力导向算法的自动布局
- 🔍 **详情展示**: 点击节点查看实体详细信息
- 📱 **响应式设计**: 自适应容器大小变化
- 🌐 **国际化支持**: 支持中英文切换

## 数据结构

### KnowledgeGraphResponse
```typescript
interface KnowledgeGraphResponse {
  code: number
  msg: string
  data: {
    graph: KnowledgeGraphData
    mind_map: Record<string, unknown>
  }
  error: string
  time: number
}
```

### KnowledgeNode
```typescript
interface KnowledgeNode {
  id: string                    // 节点唯一标识
  entity_name: string          // 实体名称
  entity_type: string          // 实体类型 (ORGANIZATION, PERSON, EVENT, CATEGORY, etc.)
  description: string          // 实体描述
  pagerank: number            // PageRank 重要度分数
  source_id: string[]         // 来源文档ID列表
}
```

### KnowledgeEdge
```typescript
interface KnowledgeEdge {
  src_id: string              // 源节点ID
  tgt_id: string             // 目标节点ID
  description: string         // 关系描述
  keywords: string[]          // 关键词
  weight: number             // 关系权重
  source_id: string[]        // 来源文档ID列表
  source: string             // 源节点名称
  target: string             // 目标节点名称
}
```

## 使用方法

### 基础用法

```tsx
import KnowledgeGraph from './components/KnowledgeGraph'

const MyComponent = () => {
  const [graphData, setGraphData] = useState<KnowledgeGraphResponse>()
  const [loading, setLoading] = useState(false)

  return (
    <KnowledgeGraph 
      data={graphData} 
      loading={loading} 
    />
  )
}
```

### 完整示例

```tsx
import React, { useState, useEffect } from 'react'
import { Row } from 'antd'
import KnowledgeGraph from './components/KnowledgeGraph'

const KnowledgeBasePage = () => {
  const [data, setData] = useState()
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchKnowledgeGraph = async () => {
      setLoading(true)
      try {
        const response = await api.getKnowledgeGraph(knowledgeBaseId)
        setData(response)
      } catch (error) {
        console.error('Failed to fetch knowledge graph:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchKnowledgeGraph()
  }, [knowledgeBaseId])

  return (
    <Row gutter={[16, 16]}>
      <KnowledgeGraph data={data} loading={loading} />
    </Row>
  )
}
```

## 组件属性

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| data | KnowledgeGraphResponse | undefined | 知识图谱数据 |
| loading | boolean | false | 加载状态 |

## 实体类型颜色

组件内置了以下实体类型的颜色映射：

- `ORGANIZATION`: #155EEF (蓝色)
- `PERSON`: #4DA8FF (浅蓝色)
- `EVENT`: #9C6FFF (紫色)
- `CATEGORY`: #8BAEF7 (淡蓝色)
- `LOCATION`: #369F21 (绿色)
- `TIME`: #FF5D34 (橙红色)
- `CONCEPT`: #FF8A4C (橙色)
- `OTHER`: #FFB048 (黄色)

## 交互功能

1. **节点点击**: 点击节点查看实体详细信息
2. **拖拽**: 拖拽节点调整位置
3. **缩放**: 鼠标滚轮缩放图谱
4. **悬停**: 悬停显示节点和边的详细信息
5. **高亮**: 点击节点高亮相邻节点和边

## 国际化

组件使用以下翻译键：

- `knowledgeBase.knowledgeGraph`: 知识图谱标题
- `knowledgeBase.entityDetails`: 实体详情标题
- `knowledgeBase.entityDetailEmpty`: 空状态提示
- `knowledgeBase.entityDetailEmptyDesc`: 空状态描述
- `knowledgeBase.entityDescription`: 实体描述标签
- `knowledgeBase.sourceDocuments`: 来源文档标签
- `userMemory.click/drag/zoom`: 操作说明

## 性能优化

- 使用 `React.memo` 避免不必要的重渲染
- 使用 `ResizeObserver` 监听容器大小变化
- 使用 `requestAnimationFrame` 优化图表重绘
- 延迟更新和懒加载提升大数据集性能

## 注意事项

1. 确保传入的数据结构符合 `KnowledgeGraphResponse` 接口
2. 节点的 `pagerank` 值用于计算节点大小，建议范围在 0-1 之间
3. 边的 `weight` 值用于计算连线粗细，建议使用正数
4. 大数据集可能影响渲染性能，建议进行数据分页或过滤