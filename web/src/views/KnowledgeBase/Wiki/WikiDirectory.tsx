import { useCallback } from 'react';
import { Alert, Button, Empty, Pagination, Skeleton } from 'antd';
import { getWikiPages, WIKI_PAGE_SIZE, type WikiPage } from '@/api/wiki';
import { useWikiResource } from './useWikiResource';

interface Props {
  kbId: string; query: string; page: number; enabled: boolean; types?: string;
  onPageChange: (page: number) => void; onSelect: (id: string) => void; onSettings: () => void; onFiles: () => void;
}

export default function WikiDirectory({ kbId, query, page, enabled, types, onPageChange, onSelect, onSettings, onFiles }: Props) {
  const summaries = types === 'summary';
  const load = useCallback((signal: AbortSignal) => getWikiPages(kbId, query, page, signal, types), [kbId, query, page, types]);
  const { data, loading, error, reload } = useWikiResource(load);
  const groups = new Map<string, { title: string; pages: WikiPage[] }>();
  for (const item of data?.items ?? []) {
    const key = summaries ? '' : item.folder_id ?? '';
    if (!groups.has(key)) groups.set(key, { title: summaries ? '' : item.folder_path.map(folder => folder.name).join(' / ') || '未分类', pages: [] });
    groups.get(key)!.pages.push(item);
  }

  return <article className="wiki-article wiki-directory">
    <h1>{query ? '搜索结果' : summaries ? '摘要文档' : 'Wiki 目录'}</h1>
    <p className="wiki-subtitle">{query ? `“${query}” · ${data?.total ?? 0} 个页面` : summaries ? '查看知识库中已生成的文档摘要。' : '查看知识库中的实体与概念页面。'}</p>
    {loading ? <Skeleton active paragraph={{ rows: 9 }} /> : error
      ? <Alert type="error" showIcon message="Wiki 页面加载失败" description={error} action={<Button onClick={reload}>重试</Button>} />
      : data?.items.length ? <>
          {[...groups.entries()].map(([key, group]) => <section className="wiki-directory-group" key={key}>
            {group.title ? <h2>{group.title}</h2> : null}
            <ul>{group.pages.map(item => <li key={item.page_id}>
              <button className="wiki-text-link" onClick={() => onSelect(item.page_id)}>{item.title}</button>
              {item.summary ? <span> — {item.summary}</span> : null}
            </li>)}</ul>
          </section>)}
          {data.total > WIKI_PAGE_SIZE ? <Pagination current={page} pageSize={WIKI_PAGE_SIZE} total={data.total}
            showSizeChanger={false} onChange={onPageChange} showTotal={total => `共 ${total} 个页面`} /> : null}
        </> : data && data.total > 0 && page > 1
          ? <Empty description="当前页已无内容，页面数量可能已变化。"><Button onClick={() => onPageChange(1)}>返回第一页</Button></Empty>
          : <Empty description={query ? '没有匹配的 Wiki 页面，试试其他关键词。' : enabled
          ? '暂无可读的 Wiki 页面。导入文档或在文件列表中查看生成进度。' : '启用 Wiki 后，将为已有文档补录，并自动处理后续导入的文档。'}>
          {!query ? <Button type="primary" onClick={enabled ? onFiles : onSettings}>{enabled ? '查看文件' : '设置 Wiki'}</Button> : null}
        </Empty>}
  </article>;
}
