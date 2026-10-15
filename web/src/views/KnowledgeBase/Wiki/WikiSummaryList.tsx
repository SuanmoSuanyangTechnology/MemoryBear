import { useCallback, useState } from 'react';
import { Button, Spin } from 'antd';
import { getWikiPages, type WikiPage } from '@/api/wiki';
import { useWikiResource } from './useWikiResource';

interface Props { kbId: string; query: string; selectedId?: string; onSelect: (id: string) => void }

/** Summary pages belong to source documents, so list them without knowledge folders. */
export default function WikiSummaryList({ kbId, query, selectedId, onSelect }: Props) {
  const [page, setPage] = useState(1);
  const [previous, setPrevious] = useState<WikiPage[]>([]);
  const load = useCallback((signal: AbortSignal) => getWikiPages(kbId, query, page, signal, 'summary'), [kbId, query, page]);
  const { data, loading, error, reload } = useWikiResource(load);
  const pages = [...new Map([...previous, ...(data?.items ?? [])].map(item => [item.page_id, item])).values()];

  return <ul className="wiki-tree" aria-label="摘要文档列表">
    {pages.map(item => <li key={item.page_id}>
      <button className={`wiki-tree-page ${selectedId === item.page_id ? 'is-selected' : ''}`} title={item.title}
        aria-current={selectedId === item.page_id ? 'page' : undefined} onClick={() => onSelect(item.page_id)}>{item.title}</button>
    </li>)}
    {loading ? <li className="wiki-tree-feedback"><Spin size="small" /></li> : null}
    {error ? <li className="wiki-tree-feedback"><span>摘要列表加载失败</span><Button type="link" size="small" onClick={reload}>重试</Button></li> : null}
    {!loading && !error && !pages.length ? <li className="wiki-tree-feedback">{query ? '没有匹配的摘要文档' : '暂无可读的摘要文档'}</li> : null}
    {data?.has_next ? <li><Button type="link" size="small" onClick={() => { setPrevious(pages); setPage(value => value + 1); }}>加载更多</Button></li> : null}
  </ul>;
}
