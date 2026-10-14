import { useCallback } from 'react';
import { Alert, Button, Skeleton, Tag } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getWikiDetail, WIKI_POLL_INTERVAL, type WikiPageStatus, type WikiPageType } from '@/api/wiki';
import { useWikiResource } from './useWikiResource';
import { remarkWikiLinks } from './wikiMarkdown';

const TYPE_LABELS: Record<WikiPageType, string> = { summary: '文档摘要', entity: '实体页面', concept: '概念页面' };
const STATUS_LABELS: Record<WikiPageStatus, string> = {
  ready: '可阅读', pending: '正在生成页面', updating: '页面更新中', cleanup_pending: '正在更新页面来源', failed: '页面生成失败', unavailable: '页面暂不可用',
};
const REASON_LABELS: Record<string, string> = {
  WIKI_UPDATING: '页面正在根据最新来源更新。',
  WIKI_SOURCE_UNAVAILABLE: '页面来源已不可用，暂时无法展示正文。',
  WIKI_SOURCE_FAILED: '源文档处理失败，请在文件列表中检查并重试。',
};

interface Props { kbId: string; pageId: string; onSelect: (id: string) => void; onDocument: (id: string) => void; onGraph: (id: string) => void }

export default function WikiReader({ kbId, pageId, onSelect, onDocument, onGraph }: Props) {
  const load = useCallback((signal: AbortSignal) => getWikiDetail(kbId, pageId, signal), [kbId, pageId]);
  // Refresh metadata even when the content version stays unchanged.
  const { data, loading, error, reload } = useWikiResource(load, WIKI_POLL_INTERVAL);
  if (loading) return <article className="wiki-article"><Skeleton active paragraph={{ rows: 12 }} /></article>;
  if (error || !data) return <article className="wiki-article"><Alert type="error" showIcon message="无法读取 Wiki 页面"
    description={error || '页面可能已被移除或无权访问。'} action={<Button onClick={reload}>重试</Button>} /></article>;
  const readable = data.status === 'ready' && data.content !== null;
  return <article className="wiki-article wiki-reader">
    {data.folder_path.length ? <div className="wiki-folder-path">{data.folder_path.map(folder => folder.name).join(' / ')}</div> : null}
    <h1>{data.title}</h1>
    <div className="wiki-page-meta">
      <Tag>{TYPE_LABELS[data.type]}</Tag><span>版本 {data.version}</span>
      {data.updated_at_ms ? <time dateTime={new Date(data.updated_at_ms).toISOString()}>
        更新于 {new Date(data.updated_at_ms).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}
      </time> : null}
      <Button type="link" onClick={() => onGraph(pageId)}>查看关联图谱</Button>
    </div>
    {readable ? <>
      <div className="wiki-markdown"><ReactMarkdown remarkPlugins={[remarkGfm, remarkWikiLinks]} skipHtml components={{
        a: ({ href, children }) => {
          const target = href?.match(/^\/wiki-page\/([0-9a-f-]{36})$/i)?.[1];
          if (target) return <button className="wiki-text-link" onClick={() => onSelect(target)}>{children}</button>;
          if (href?.startsWith('#wiki-source-') && data.sources.some(source => href === `#wiki-source-${source.ref}`)) {
            return <button className="wiki-citation" onClick={() => {
              const element = document.getElementById(href.slice(1));
              element?.scrollIntoView({ behavior: 'smooth', block: 'center' });
              element?.focus({ preventScroll: true });
            }}>{children}</button>;
          }
          return <span>{children}</span>;
        },
        img: ({ alt }) => <span>{alt}</span>,
      }}>{data.content}</ReactMarkdown></div>
      {data.sources.length ? <section className="wiki-sources"><h2>来源引用</h2>
        <ol>{data.sources.map(source => <li key={source.ref} id={`wiki-source-${source.ref}`} tabIndex={-1}>
          <div><span className="wiki-source-ref">[{source.ref}]</span><button className="wiki-text-link" onClick={() => onDocument(source.document_id)}>
            {source.document_title || '查看原文档'}</button></div>
          <blockquote>{source.quote}</blockquote>
          <span className="wiki-source-chunk">分块 ID：{source.chunk_id}</span>
        </li>)}</ol>
      </section> : null}
      {data.outlinks.length ? <section className="wiki-related"><h2>相关页面</h2>
        {data.outlinks.map(link => <button className="wiki-text-link" key={link.page_id} onClick={() => onSelect(link.page_id)}>{link.title}</button>)}
      </section> : null}
    </> : <Alert showIcon type={data.status === 'failed' ? 'error' : 'info'} message={STATUS_LABELS[data.status]}
      description={<>{data.reason ? <p>{REASON_LABELS[data.reason] || '处理暂未完成，请在文件列表中查看详细进度。'}</p> : null}<p>页面状态会自动刷新。可到文件列表查看处理进度，失败后重新生成。</p></>}
      action={<Button onClick={reload}>刷新</Button>} />}
  </article>;
}
