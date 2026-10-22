import { useTranslation } from 'react-i18next';
import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Button, Flex, Skeleton, Tag } from 'antd';
import { BulbOutlined, FileTextOutlined, TagOutlined } from '@ant-design/icons';
import RbAlert from '@/components/RbAlert';
import type { Components } from 'react-markdown';
import RbMarkdown from '@/components/Markdown';
import { remarkWikiLinks } from '../utils/wikiMarkdown';
import type { BreadcrumbPath } from '@/hooks/useBreadcrumbManager';
import type { WikiPageDetail } from './types';
import { formatDateTime } from '@/utils/format'

interface WikiArticleProps {
  breadcrumbPath: BreadcrumbPath;
  detail: WikiPageDetail | null;
  markdown: string;
  version?: number;
  loading: boolean;
  error: boolean;
  onReload: () => void;
  onTitleClick?: () => void;
  onPageSelect: (id: string) => void;
}

const typeIcons = { summary: <FileTextOutlined />, entity: <TagOutlined />, concept: <BulbOutlined /> };

export default function WikiArticle({
  breadcrumbPath, detail, markdown, version, loading, error, onReload, onPageSelect, onTitleClick
}: WikiArticleProps) {
  const { t } = useTranslation();
  const statusLabels: Record<string, string> = {
    pending: t('knowledgeBase.privateWiki.pending'),
    updating: t('knowledgeBase.privateWiki.updating'),
    cleanup_pending: t('knowledgeBase.privateWiki.cleanup'),
    failed: t('knowledgeBase.privateWiki.failed'),
    unavailable: t('knowledgeBase.privateWiki.unavailable'),
  };
  const reasonLabels: Record<string, string> = {
    WIKI_UPDATING: t('knowledgeBase.privateWiki.updatingReason'),
    WIKI_SOURCE_UNAVAILABLE: t('knowledgeBase.privateWiki.unavailableReason'),
    WIKI_SOURCE_FAILED: t('knowledgeBase.privateWiki.failedReason'),
  };
  const typeLabels = { summary: t('knowledgeBase.privateWiki.summary'), entity: t('knowledgeBase.privateWiki.entity'), concept: t('knowledgeBase.privateWiki.concept') };

  const { knowledgeBaseId } = useParams<{ knowledgeBaseId: string }>();
  const articleRef = useRef<HTMLElement>(null);
  const [outline, setOutline] = useState<{ id: string; title: string; level: number }[]>([]);
  useEffect(() => {
    setOutline(Array.from(articleRef.current?.querySelectorAll<HTMLHeadingElement>('.wiki-markdown h2, .wiki-markdown h3') ?? [])
      .map((heading) => ({ id: heading.id, title: heading.textContent ?? '', level: Number(heading.tagName.slice(1)) })));
  }, [markdown, detail?.status, loading, error]);

  if (loading || !detail) return <Skeleton active paragraph={{ rows: 12 }} />;
  if (error) {
    return (
      <RbAlert color="red">
        <Flex align="center" justify="space-between" gap={12} className="rb:flex-1 rb:min-w-0" role="alert">
          <span>{t('knowledgeBase.privateWiki.loadFailed')}</span>
          <Button onClick={onReload}>{t('knowledgeBase.privateWiki.retry')}</Button>
        </Flex>
      </RbAlert>
    );
  }

  const readable = detail.status === 'ready' && detail.content !== null;
  const groups = new Map<string, WikiPageDetail['sources']>();
  detail.sources.forEach((source) => {
    const excerpts = groups.get(source.document_id) ?? [];
    excerpts.push(source);
    groups.set(source.document_id, excerpts);
  });
  const jumpTo = (id: string) => {
    const element = Array.from(articleRef.current?.querySelectorAll<HTMLElement>('[id]') ?? [])
      .find((item) => item.id === id);
    if (element instanceof HTMLDetailsElement) element.open = true;
    element?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    element?.focus({ preventScroll: true });
  };

  const linkComponents: Components = {
    a: ({ href, children }) => {
      console.log('linkComponents href', href, children)
      const target = href?.match(/^\/wiki-page\/([0-9a-f-]{36})$/i)?.[1];
      if (target) {
        return <Button type="link" className="rb:h-auto! rb:p-0!" onClick={() => onPageSelect(target)}>{children}</Button>;
      }
      if (href?.startsWith('#wiki-source-') && detail.sources.some((source) => href === `#wiki-source-${source.ref}`)) {
        return <Button type="link" className="rb:h-auto! rb:p-0!" onClick={() => jumpTo(href.slice(1))}>{children}</Button>;
      }
      return <span>{children}</span>;
    },
    img: ({ alt }) => <span>{alt}</span>,
  };

  return (
    <Flex vertical gap={16} ref={articleRef} className="rb:h-full! rb:overflow-hidden!">
      <h1 className="rb:text-2xl rb:font-semibold">
        {onTitleClick ? (
          <Flex
            align="center"
            gap={8}
            className="rb:cursor-pointer rb:hover:text-blue-500 rb:group"
            onClick={onTitleClick}
          >
            {detail.title}
            <div className="rb:size-6 rb:bg-cover rb:bg-[url('@/assets/images/menuNew/arrow_t_r.svg')] rb:group-hover:bg-[url('@/assets/images/menuNew/arrow_t_r_blue.svg')]" />
          </Flex>
        ) : detail.title}
      </h1>
      <Flex gap={8}>
        <Tag bordered={false} icon={typeIcons[detail.type]}>{typeLabels[detail.type]}</Tag>
        <Tag bordered={false}>v{version ?? detail.version}</Tag>
        <Tag bordered={false}>{t('knowledgeBase.privateWiki.updatedAt', { time: formatDateTime(detail.updated_at_ms) })}</Tag>
      </Flex>
      <div className="rb:flex-1 rb:overflow-y-auto!">
        {readable ? (
          <>
            {detail.summary &&
              <blockquote className="rb:border-l-4 rb:border-gray-200 rb:pl-4 rb:text-gray-500">
                <RbMarkdown
                  content={detail.summary}
                  remarkPlugins={[remarkWikiLinks]}
                  skipHtml
                  components={linkComponents}
                />
              </blockquote>
            }
            {outline.length > 3 && (
              <details>
                <summary className="rb:cursor-pointer">{t('knowledgeBase.privateWiki.outline')}</summary>
                <nav aria-label={t('knowledgeBase.privateWiki.outline')}>
                  {outline.map((heading) => (
                    <div key={heading.id} style={{ paddingLeft: heading.level === 3 ? 16 : 0 }}>
                      <Button type="link" onClick={() => jumpTo(heading.id)}>{heading.title}</Button>
                    </div>
                  ))}
                </nav>
              </details>
            )}
            <div className="wiki-markdown rb:prose rb:max-w-none">
              <RbMarkdown
                content={markdown}
                remarkPlugins={[remarkWikiLinks]}
                skipHtml
                components={{
                  h2: ({ node, children }) => <h2 id={`wiki-heading-${node?.position?.start.offset}`} tabIndex={-1}>{children}</h2>,
                  h3: ({ node, children }) => <h3 id={`wiki-heading-${node?.position?.start.offset}`} tabIndex={-1}>{children}</h3>,
                  ...linkComponents,
                }}
              />
            </div>
            {groups.size > 0 && (
              <section className="rb:mt-4">
                <h2 className="rb:text-lg rb:font-medium rb:mb-3!">{t('knowledgeBase.privateWiki.sources')}</h2>
                <ul className="rb:pl-6! rb:list-disc">
                  {[...groups.entries()].map(([documentId, excerpts]) => (
                    <li key={documentId}>
                      <div className="rb:font-medium">
                        {knowledgeBaseId && documentId ? (
                          <Button
                            type="link"
                            size="small"
                            onClick={() => {
                              const params = new URLSearchParams({
                                documentId,
                                parentId: breadcrumbPath.documentFolderPath.at(-1)?.id || knowledgeBaseId,
                                breadcrumbPath: JSON.stringify({
                                  ...breadcrumbPath,
                                  document: {
                                    id: documentId,
                                    name: excerpts[0].document_title || t('knowledgeBase.privateWiki.sources'),
                                    type: 'document',
                                  },
                                }),
                              });
                              const url = new URL(window.location.href);
                              url.hash = `/knowledge-base/${knowledgeBaseId}/DocumentDetails?${params}`;
                              window.open(url.href, '_blank', 'noopener,noreferrer');
                            }}
                          >
                            {excerpts[0].document_title || t('knowledgeBase.privateWiki.sources')}
                          </Button>
                        ) : (
                          excerpts[0].document_title || t('knowledgeBase.privateWiki.sources')
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {detail.outlinks.length > 0 && (
              <section className="rb:mt-4">
                <h2 className="rb:text-lg rb:font-medium">{t('knowledgeBase.privateWiki.related')}</h2>
                <ul className="rb:list-disc! rb:pl-6!">
                  {detail.outlinks.map((link) => (
                    <li key={link.page_id}>
                      <Button type="link" size="small" onClick={() => onPageSelect(link.page_id)}>{link.title}</Button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {!markdown.trim() && (
              <RbAlert color="blue">
                <Flex vertical gap={4} role="alert">
                  <span>{t('knowledgeBase.privateWiki.empty')}</span>
                  <span>{t('knowledgeBase.privateWiki.emptyHint')}</span>
                </Flex>
              </RbAlert>
            )}
          </>
        ) : (
          <RbAlert
            color={detail.status === 'failed' ? 'red' : 'blue'}
          >
            <Flex align="center" justify="space-between" gap={12} className="rb:flex-1 rb:min-w-0" role="alert">
              <Flex vertical gap={8} className="rb:min-w-0">
                <span className="rb:font-semibold">{statusLabels[detail.status] ?? t('knowledgeBase.privateWiki.unavailable')}</span>
                <span className="rb:whitespace-pre-line">
                  {reasonLabels[detail.reason] ?? t('knowledgeBase.privateWiki.progressHint')}
                </span>
              </Flex>
              <Button onClick={onReload}>{t('knowledgeBase.privateWiki.refresh')}</Button>
            </Flex>
          </RbAlert>
        )}
      </div>
    </Flex>
  );
}
