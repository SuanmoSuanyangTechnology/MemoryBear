import { useCallback, useMemo } from 'react';
import { Alert, Button, Empty, Skeleton } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { getWikiGraph, type WikiPageType } from '@/api/wiki';
import { useWikiResource } from './useWikiResource';

const CATEGORIES: { type: WikiPageType; name: string; color: string }[] = [
  { type: 'summary', name: '文档摘要', color: '#73a1ef' },
  { type: 'entity', name: '实体页面', color: '#57b99f' },
  { type: 'concept', name: '概念页面', color: '#a690dc' },
];
interface Props { kbId: string; centerId?: string; onSelect: (id: string) => void; onOverview: () => void }

export default function WikiGraph({ kbId, centerId, onSelect, onOverview }: Props) {
  const load = useCallback((signal: AbortSignal) => getWikiGraph(kbId, centerId, signal), [kbId, centerId]);
  const { data, loading, error, reload } = useWikiResource(load);
  const option = useMemo<EChartsOption>(() => ({
    tooltip: { trigger: 'item', renderMode: 'richText' },
    legend: { bottom: 12, data: CATEGORIES.map(item => item.name) },
    series: [{
      type: 'graph', layout: 'force', roam: true, draggable: true,
      data: data?.nodes.map(node => ({
        id: node.id, name: node.title, category: CATEGORIES.findIndex(item => item.type === node.type),
        symbolSize: node.id === centerId ? 44 : 28,
      })),
      links: data?.edges,
      categories: CATEGORIES.map(item => ({ name: item.name, itemStyle: { color: item.color } })),
      label: { show: true, position: 'right', color: '#535862', fontSize: 12, width: 160, overflow: 'truncate' },
      emphasis: { focus: 'adjacency' },
      lineStyle: { color: '#cbd4e3', curveness: 0.1 },
      edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 6,
      force: { repulsion: 220, edgeLength: [90, 150], gravity: 0.08 },
    }],
  }), [data, centerId]);

  return <section className="wiki-graph-view">
    <div className="wiki-graph-heading"><div><h1>{centerId ? '页面关联图谱' : 'Wiki 图谱'}</h1>
      <p>点击节点阅读页面，拖动或缩放探索关联。</p></div>
      {centerId ? <Button onClick={onOverview}>查看全部图谱</Button> : null}
    </div>
    {loading ? <Skeleton active /> : error ? <Alert type="error" showIcon message="图谱加载失败" description={error} action={<Button onClick={reload}>重试</Button>} />
      : !data?.nodes.length ? <Empty description="暂无可读的 Wiki 图谱节点" /> : <>
        <div className="wiki-graph-counts">当前展示 {data.returned_nodes} 个页面、{data.returned_edges} 条关联 · 全库 {data.total_nodes} 个页面、{data.total_edges} 条关联</div>
        {data.truncated ? <Alert type="info" showIcon message="图谱已达到展示上限，仅显示部分节点和关联。可进入具体页面查看关联图谱。" /> : null}
        <ReactECharts option={option} notMerge style={{ height: '100%', minHeight: 360, flex: 1 }}
          onEvents={{ click: (event: { dataType?: string; data?: { id?: string } }) => {
            if (event.dataType === 'node' && event.data?.id) onSelect(event.data.id);
          } }} />
        <details className="wiki-graph-page-list"><summary>图谱页面列表（{data.returned_nodes}）</summary>
          {data.nodes.map(node => <button key={node.id} className="wiki-text-link" onClick={() => onSelect(node.id)}>{node.title}</button>)}
        </details>
      </>}
  </section>;
}
