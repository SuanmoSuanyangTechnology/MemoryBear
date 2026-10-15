import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Alert, Button, Skeleton, Tag } from 'antd';
import { BulbOutlined, FileTextOutlined, TagOutlined } from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { getWikiDetail, WIKI_POLL_INTERVAL, type WikiDetail, type WikiPageStatus, type WikiPageType } from '@/api/wiki';
import { useWikiResource } from './useWikiResource';
import { remarkWikiLinks } from './wikiMarkdown';
import { groupWikiSources } from './wikiSources';

const TYPE_LABELS: Record<WikiPageType, string> = { summary: '摘要', entity: '实体', concept: '概念' };
const TYPE_ICONS = { summary: <FileTextOutlined />, entity: <TagOutlined />, concept: <BulbOutlined /> };
const STATUS_LABELS: Record<WikiPageStatus, string> = {
  ready: '可阅读', pending: '正在生成页面', updating: '页面更新中', cleanup_pending: '正在更新页面来源', failed: '页面生成失败', unavailable: '页面暂不可用',
};
const REASON_LABELS: Record<string, string> = {
  WIKI_UPDATING: '页面正在根据最新来源更新。',
  WIKI_SOURCE_UNAVAILABLE: '页面来源已不可用，暂时无法展示正文。',
  WIKI_SOURCE_FAILED: '源文档处理失败，请在文件列表中检查并重试。',
};

interface Props {
  kbId: string; pageId: string; onSelect: (id: string) => void; onDocument: (id: string) => void; onGraph: (id: string) => void;
  embedded?: boolean; onLoaded?: (page: WikiDetail) => void;
}

export default function WikiReader({ kbId, pageId, onSelect, onDocument, onGraph, embedded = false, onLoaded }: Props) {
  const articleRef = useRef<HTMLElement>(null);
  const [outline, setOutline] = useState<{ id: string; title: string; level: number }[]>([]);
  const load = useCallback((signal: AbortSignal) => getWikiDetail(kbId, pageId, signal), [kbId, pageId]);
  const { data, loading, error, reload } = useWikiResource(load, WIKI_POLL_INTERVAL,
    page => ['pending', 'updating', 'cleanup_pending'].includes(page.status));
  useLayoutEffect(() => { if (data) onLoaded?.(data); }, [data, onLoaded]);
  useEffect(() => {
    if (!data?.content || data.status !== 'ready') return;
    setOutline(Array.from(articleRef.current?.querySelectorAll<HTMLHeadingElement>('.wiki-markdown h2, .wiki-markdown h3') ?? [])
      .map(heading => ({ id: heading.id, title: heading.textContent ?? '', level: Number(heading.tagName.slice(1)) })));
  }, [data?.content, data?.status]);
  if (loading) return <article className="wiki-article"><Skeleton active paragraph={{ rows: 12 }} /></article>;
  if (error || !data) return <article className="wiki-article"><Alert type="error" showIcon message="无法读取 Wiki 页面"
    description={error || '页面可能已被移除或无权访问。'} action={<Button onClick={reload}>重试</Button>} /></article>;
  const readable = data.status === 'ready' && data.content !== null;
  const sources = groupWikiSources(data.sources);
  const jumpTo = (id: string) => {
    const element = articleRef.current?.querySelector<HTMLElement>(`[id="${id}"]`);
    if (element instanceof HTMLDetailsElement) element.open = true;
    element?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    element?.focus({ preventScroll: true });
  };
  return <article ref={articleRef} className={`wiki-article wiki-reader${embedded ? ' wiki-reader-embedded' : ''}`}>
    <h1>{data.title}</h1>
    <div className="wiki-page-badges" aria-label="页面类型与版本">
      <Tag bordered={false} icon={TYPE_ICONS[data.type]}>{TYPE_LABELS[data.type]}</Tag>
      <Tag bordered={false}>v{data.version}</Tag>
    </div>
    {readable ? <>
      {data.summary ? <blockquote className="wiki-page-summary">{data.summary}</blockquote> : null}
      {outline.length > 3 ? <details className="wiki-outline"><summary>本页目录</summary>
        <nav aria-label="本页目录">{outline.map(heading => <button key={heading.id} className={`wiki-outline-level-${heading.level}`}
          onClick={() => jumpTo(heading.id)}>{heading.title}</button>)}</nav>
      </details> : null}
      <div className="wiki-markdown"><ReactMarkdown remarkPlugins={[remarkGfm, remarkWikiLinks]} skipHtml components={{
        h2: ({ node, children }) => <h2 id={`wiki-heading-${node?.position?.start.offset}`} tabIndex={-1}>{children}</h2>,
        h3: ({ node, children }) => <h3 id={`wiki-heading-${node?.position?.start.offset}`} tabIndex={-1}>{children}</h3>,
        a: ({ href, children }) => {
          const target = href?.match(/^\/wiki-page\/([0-9a-f-]{36})$/i)?.[1];
          if (target) return <button className="wiki-text-link" onClick={() => onSelect(target)}>{children}</button>;
          if (href?.startsWith('#wiki-source-') && data.sources.some(source => href === `#wiki-source-${source.ref}`)) {
            return <button className="wiki-citation" onClick={() => jumpTo(href.slice(1))}>{children}</button>;
          }
          return <span>{children}</span>;
        },
        img: ({ alt }) => <span>{alt}</span>,
      }}>{data.content}</ReactMarkdown></div>
      {sources.length ? <section className="wiki-sources"><h2>来源文件</h2>
        <ul>{sources.map(file => <li key={file.documentId}>
          <button className="wiki-source-file" onClick={() => onDocument(file.documentId)}>{file.title}</button>
          {file.excerpts.map(source => <details className="wiki-source-excerpt" key={source.ref} id={`wiki-source-${source.ref}`} tabIndex={-1}>
            <summary>引用片段 [{source.ref}]</summary><blockquote>{source.quote}</blockquote>
            <span className="wiki-source-chunk">分块 ID：{source.chunk_id}</span>
          </details>)}
        </li>)}</ul>
      </section> : null}
      {data.outlinks.length ? <section className="wiki-related"><h2>相关页面</h2>
        {data.outlinks.map(link => <button className="wiki-text-link" key={link.page_id} onClick={() => onSelect(link.page_id)}>{link.title}</button>)}
      </section> : null}
      {!data.content?.trim() ? <Alert type="info" showIcon message="页面暂无正文" description="可以查看来源文件，或在文件列表中重新生成。" /> : null}
    </> : <Alert showIcon type={data.status === 'failed' ? 'error' : 'info'} message={STATUS_LABELS[data.status]}
      description={<>{data.reason ? <p>{REASON_LABELS[data.reason] || '处理暂未完成，请在文件列表中查看详细进度。'}</p> : null}<p>可到文件列表查看处理进度，失败后重新生成。</p></>}
      action={<Button onClick={reload}>刷新</Button>} />}
    <details className="wiki-page-information"><summary>页面信息</summary><div>
      {data.updated_at_ms ? <time dateTime={new Date(data.updated_at_ms).toISOString()}>
        更新于 {new Date(data.updated_at_ms).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}
      </time> : null}
      <Button type="link" onClick={() => onGraph(pageId)}>查看关联图谱</Button>
    </div></details>
  </article>;
}
