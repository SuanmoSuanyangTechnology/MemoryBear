import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Drawer, Empty, Input, Select, Skeleton, Space, Switch } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { getWikiGraph, type WikiDetail, type WikiPageType } from '@/api/wiki';
import { useWikiResource } from './useWikiResource';
import WikiReader from './WikiReader';

const CATEGORIES: { type: WikiPageType; name: string; color: string }[] = [
  { type: 'summary', name: '文档摘要', color: '#73a1ef' },
  { type: 'entity', name: '实体页面', color: '#57b99f' },
  { type: 'concept', name: '概念页面', color: '#a690dc' },
];
interface Props { kbId: string; centerId?: string; types?: string; onSelect: (id: string) => void; onDocument: (id: string) => void; onOverview: () => void }

export default function WikiGraph({ kbId, centerId, types, onSelect, onDocument, onOverview }: Props) {
  const chartRef = useRef<ReactECharts>(null);
  const [selectedId, setSelectedId] = useState<string>();
  const [focusId, setFocusId] = useState(centerId);
  const [depth, setDepth] = useState<1 | 2>(1);
  const [arrows, setArrows] = useState(true);
  const [search, setSearch] = useState('');
  useEffect(() => { setFocusId(centerId); }, [centerId]);
  const load = useCallback((signal: AbortSignal) => getWikiGraph(kbId, focusId, signal, depth, types), [kbId, focusId, depth, types]);
  const { data, loading, error, reload } = useWikiResource(load);
  const handlePageLoaded = useCallback((page: WikiDetail) => { if (page.status === 'unavailable') reload(); }, [reload]);
  const adjacent = useMemo(() => {
    const ids = new Set([selectedId]);
    for (const edge of data?.edges ?? []) {
      if (edge.source === selectedId) ids.add(edge.target);
      if (edge.target === selectedId) ids.add(edge.source);
    }
    return ids;
  }, [data, selectedId]);
  const option = useMemo<EChartsOption>(() => ({
    animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    tooltip: { trigger: 'item', renderMode: 'richText' },
    legend: { bottom: 12, data: CATEGORIES.map(item => item.name) },
    series: [{
      type: 'graph', layout: 'force', roam: true, draggable: true,
      data: data?.nodes.map(node => ({
        id: node.id, name: node.title, category: CATEGORIES.findIndex(item => item.type === node.type),
        symbolSize: node.id === selectedId || node.id === focusId ? 40 : 28,
        itemStyle: { opacity: !selectedId || adjacent.has(node.id) ? 1 : .2 },
        label: { opacity: !selectedId || adjacent.has(node.id) ? 1 : .25 },
      })),
      links: data?.edges.map(edge => ({ ...edge, lineStyle: { opacity: !selectedId || edge.source === selectedId || edge.target === selectedId ? .8 : .12 } })),
      categories: CATEGORIES.map(item => ({ name: item.name, itemStyle: { color: item.color } })),
      label: { show: true, position: 'right', color: '#535862', fontSize: 12, width: 160, overflow: 'truncate' },
      emphasis: { focus: 'adjacency' },
      lineStyle: { color: '#cbd4e3', curveness: 0.1 },
      edgeSymbol: ['none', arrows ? 'arrow' : 'none'], edgeSymbolSize: 6,
      force: { repulsion: 220, edgeLength: [90, 150], gravity: 0.08 },
    }],
  }), [data, selectedId, focusId, adjacent, arrows]);

  const selectNode = (id: string) => {
    setSelectedId(id);
    const dataIndex = data?.nodes.findIndex(node => node.id === id) ?? -1;
    if (dataIndex >= 0) chartRef.current?.getEchartsInstance().dispatchAction({ type: 'focusNodeAdjacency', seriesIndex: 0, dataIndex });
    else setFocusId(id);
  };
  const resetViewport = () => chartRef.current?.getEchartsInstance().setOption({ series: [{ center: ['50%', '50%'], zoom: 1 }] });

  return <section className="wiki-graph-view">
    <div className="wiki-graph-heading"><div><h1>{focusId ? '页面关联图谱' : 'Wiki 图谱'}</h1>
      <p>点击节点阅读页面，拖动或缩放探索关联。</p></div>
      {focusId ? <Button onClick={() => { setFocusId(undefined); onOverview(); }}>查看全部图谱</Button> : null}
    </div>
    <Space wrap className="wiki-graph-controls">
      <Input.Search value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索图中页面" aria-label="搜索图中页面"
        onSearch={value => {
          const node = data?.nodes.find(item => item.title.toLocaleLowerCase().includes(value.trim().toLocaleLowerCase()));
          if (value.trim() && node) selectNode(node.id);
        }} style={{ width: 220 }} />
      <Button onClick={resetViewport}>适应屏幕</Button>
      <label><Switch checked={arrows} onChange={setArrows} size="small" aria-label="显示有向边箭头" /> 显示箭头</label>
      {focusId ? <Select aria-label="邻居深度" value={depth} onChange={setDepth} options={[{ value: 1, label: '一层邻居' }, { value: 2, label: '两层邻居' }]} /> : null}
    </Space>
    {loading ? <Skeleton active /> : error ? <Alert type="error" showIcon message="图谱加载失败" description={error} action={<Button onClick={reload}>重试</Button>} />
      : !data?.nodes.length ? <Empty description="暂无可读的 Wiki 图谱节点" /> : <>
        <div className="wiki-graph-counts">当前展示 {data.returned_nodes} 个页面、{data.returned_edges} 条关联 · 全库 {data.total_nodes} 个页面、{data.total_edges} 条关联</div>
        {data.truncated ? <Alert type="info" showIcon message="图谱已达到展示上限，仅显示部分节点和关联。可进入具体页面查看关联图谱。" /> : null}
        <ReactECharts ref={chartRef} option={option} style={{ height: '100%', minHeight: 360, flex: 1 }}
          onEvents={{ click: (event: { dataType?: string; data?: { id?: string } }) => {
            if (event.dataType === 'node' && event.data?.id) selectNode(event.data.id);
          } }} />
        <details className="wiki-graph-page-list"><summary>图谱页面列表（{data.returned_nodes}）</summary>
          {data.nodes.filter(node => node.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
            .map(node => <button key={node.id} className="wiki-text-link" onClick={() => selectNode(node.id)}>{node.title}</button>)}
        </details>
      </>}
    <Drawer title="Wiki 页面" open={!!selectedId} onClose={() => setSelectedId(undefined)} width={560} mask={false}
      styles={{ body: { padding: 0 } }}
      extra={<Space><Select aria-label="展开邻居深度" size="small" value={depth} onChange={value => {
        setDepth(value); if (selectedId) setFocusId(selectedId);
      }} options={[{ value: 1, label: '一层' }, { value: 2, label: '两层' }]} />
        <Button onClick={() => selectedId && setFocusId(selectedId)}>展开邻居</Button>
        <Button onClick={() => selectedId && onSelect(selectedId)}>完整阅读</Button></Space>}>
      {selectedId ? <WikiReader key={selectedId} kbId={kbId} pageId={selectedId} embedded onSelect={selectNode} onDocument={onDocument}
        onGraph={setFocusId} onLoaded={handlePageLoaded} /> : null}
    </Drawer>
  </section>;
}
