/**
 * "Related Wiki" drawer for the file list: fetches Wiki pages by document_id with pagination.
 * Renders the list with PageScrollList; clicking an item opens the Wiki page.
 */
import { forwardRef, useImperativeHandle, useMemo, useState } from 'react';
import { Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { getKnowledgeWikiPagesUrl } from '@/api/kbWiki';
import PageScrollList from '@/components/PageScrollList';
import RbDrawer from '@/components/RbDrawer';
import type { KnowledgeBaseDocumentData } from '@/views/KnowledgeBase/types';
import type { PageType, WikiPageItem } from '../Wiki/types';

export interface RelatedWikiDrawerRef {
  handleOpen: (document: KnowledgeBaseDocumentData) => void;
}

interface RelatedWikiDrawerProps {
  knowledgeBaseId: string;
  onOpenWikiPage: (pageId: string) => void;
}

const PAGE_TYPE_DOT: Record<PageType, string> = {
  summary: 'rb:bg-blue-500',
  concept: 'rb:bg-purple-300',
  entity: 'rb:bg-cyan-300',
};

const RelatedWikiDrawer = forwardRef<RelatedWikiDrawerRef, RelatedWikiDrawerProps>(({
  knowledgeBaseId, onOpenWikiPage,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [document, setDocument] = useState<KnowledgeBaseDocumentData | null>(null);
  const [total, setTotal] = useState<number | null>(0);

  useImperativeHandle(ref, () => ({
    handleOpen: (item) => {
      setDocument(item);
      setTotal(typeof item.wiki_count === 'number' ? item.wiki_count : null);
      setVisible(true);
    },
  }));
  const handleClose = () => {
    setVisible(false)
  }

  const url = useMemo(() => getKnowledgeWikiPagesUrl(knowledgeBaseId), [knowledgeBaseId]);
  const query = useMemo(() => ({ document_id: document?.id ?? '' }), [document?.id]);

  const key = (name: string, params?: Record<string, string | number>) =>
    t(`knowledgeBase.${name}`, params ?? {});

  const handlePageClick = (pageId: string) => {
    setVisible(false);
    onOpenWikiPage(pageId);
  };

  return (
    <RbDrawer
      title={(
        <Flex vertical gap={2}>
          <span>{key('privateWiki.relatedWiki')}</span>
          <span className="rb:text-[12px] rb:font-normal rb:text-gray-600">
            {total === null
              ? key('documentWiki.subtitleUnknown', { name: document?.file_name ?? '' })
              : key('documentWiki.subtitle', {
                name: document?.file_name ?? '',
                count: total,
              })}
          </span>
        </Flex>
      )}
      open={visible}
      width={480}
      footer={null}
      onClose={handleClose}
    >
      {document && visible && (
        <PageScrollList<WikiPageItem, { document_id: string }>
          url={url}
          query={query}
          column={1}
          onTotalChange={setTotal}
          renderItem={(page) => {
            const displayTitle = page.title.endsWith('.md') ? page.title : `${page.title}.md`;
            return (
              <Flex
                align="center"
                gap={12}
                key={page.page_id || page.id}
                onClick={() => handlePageClick(page.page_id || page.id)}
                className="rb:w-full rb:cursor-pointer rb:border rb:border-gray-200 rb:rounded-xl rb:bg-white rb:p-4! rb:text-left rb:hover:border-[rgba(21,94,239,0.25)] rb:hover:bg-[rgba(21,94,239,0.06)]"
              >
                <div className="rb:min-w-0 rb:flex-1">
                  <Flex align="center" gap={4}>
                    <span className={clsx('rb:size-1.5 rb:rounded-full', PAGE_TYPE_DOT[page.type])} />
                    <span className="rb:text-[11px] rb:text-gray-600">
                      {key(`privateWiki.${page.type}`)}
                    </span>
                  </Flex>
                  <strong className="rb:block rb:my-2! rb:font-medium rb:text-gray-900 rb:break-all">
                    {displayTitle}
                  </strong>
                  <p className="rb:m-0! rb:text-[12px] rb:leading-relaxed rb:text-gray-600">
                    {page.summary || key('privateWiki.noDescription')}
                  </p>
                </div>
                <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:rotate-90" />
              </Flex>
            );
          }}
        />
      )}
    </RbDrawer>
  );
});

export default RelatedWikiDrawer;
