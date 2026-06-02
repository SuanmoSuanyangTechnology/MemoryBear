/**
 * @Description: Scroll List
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-18 16:19:58
 * @LastEditors: yujiangping
 * @LastEditTime: 2025-12-22 13:47:53
 */
import { FileOutlined, FieldTimeOutlined, EditOutlined } from '@ant-design/icons';
import { Skeleton, Flex, Space, App } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import type { RecallTestData } from '@/views/KnowledgeBase/types';
import { NoData } from './noData';
import { formatDateTime } from '@/utils/format';
import InfiniteScroll from 'react-infinite-scroll-component';
import RbMarkdown from '@/components/Markdown';
import { useMemo, useState, type MouseEvent } from 'react';
import { deleteDocumentChunk } from '@/api/knowledgeBase'

interface RecallTestResultProps {
  data: RecallTestData[];
  showEmpty?: boolean;
  hasMore?: boolean;
  total?: number;
  loadMore?: () => void;
  refresh?: () => void;
  loading?: boolean;
  scrollableTarget?: string;
  editable?: boolean; // Whether editable
  onItemClick?: (item: RecallTestData, index: number) => void; // Click item callback
  parserMode?: number; // Parser mode, 1 means QA format
  handleCopy?: (text?: string) => void;
  handleInsert?: (parentChunkId?: string) => void;
}

const RecallTestResult = ({ 
  data, 
  showEmpty = true,
  hasMore = false,
  total = 0,
  loadMore,
  refresh,
  loading = false,
  scrollableTarget,
  editable = false,
  onItemClick,
  parserMode = 0,
  handleCopy,
  handleInsert
}: RecallTestResultProps) => {
  const { t } = useTranslation();
  const { modal, message } = App.useApp()
  const [expandedChunks, setExpandedChunks] = useState<Record<string, boolean>>({});
  console.log('chunk data', data)

  // Parse QA format content
  const parseQAContent = (content: string) => {
    if (!content || parserMode !== 1) return null;
    
    const qaRegex = /question:\s*(.*?)\s*answer:\s*(.*?)$/s;
    const match = content.match(qaRegex);
    
    if (match) {
      const question = match[1]?.trim() || '';
      const answer = match[2]?.trim() || '';
      return { question, answer };
    }
    
    return null;
  };

  // Format QA content for display
  const formatQAContent = (question: string, answer: string) => {
    return `**${t('knowledgeBase.question')}:** ${question}\n**${t('knowledgeBase.answer')}:** ${answer}`;
  };

  // Check if content is valid HTML
  const isValidHTML = (content: string): boolean => {
    if (!content) return false;
    // Check if content contains HTML tags
    const htmlTagPattern = /<[^>]+>/;
    return htmlTagPattern.test(content);
  };

  // Render content with HTML or Markdown fallback
  const renderTextContent = useMemo(() => {
    return (content: string) => {
      // Try to render as HTML first
      if (isValidHTML(content)) {
        try {
          return (
            <div 
              className='rb:prose rb:prose-sm rb:max-w-none'
              dangerouslySetInnerHTML={{ __html: content }}
            />
          );
        } catch (error) {
          console.warn('HTML parsing failed, falling back to Markdown:', error);
        }
      }
      
      // Fallback to Markdown rendering
      return <RbMarkdown content={content} showHtmlComments={true} />;
    };
  }, []);

  const handleItemClick = (e: React.MouseEvent, item: RecallTestData, index: number) => {
    // Check if the click is on an image or image-related element
    const target = e.target as HTMLElement;
    
    // Check if clicked on image itself, image container, preview layer, close button or SVG icon
    if (
      target.tagName === 'IMG' || 
      target.tagName === 'SVG' || // SVG icon
      target.tagName === 'PATH' || // SVG path
      target.closest('.ant-image') ||
      target.closest('.ant-image-preview') ||
      target.closest('.ant-image-preview-wrap') ||
      target.closest('.ant-image-preview-operations') ||
      target.closest('.anticon') || // Ant Design icon
      target.classList.contains('ant-image-img') ||
      target.classList.contains('ant-image-mask') ||
      target.classList.contains('ant-image-preview-close') ||
      target.classList.contains('anticon')
    ) {
      return;
    }
    
    if (editable && onItemClick) {
      onItemClick(item, index);
    }
  };
  const handleChildItemClick = (e: React.MouseEvent, item: RecallTestData, childIndex: number, parentIndex: number) => {
    e.preventDefault();
    e.stopPropagation();
    const target = e.target as HTMLElement;
    
    if (
      target.tagName === 'IMG' || 
      target.tagName === 'SVG' ||
      target.tagName === 'PATH' ||
      target.closest('.ant-image') ||
      target.closest('.ant-image-preview') ||
      target.closest('.ant-image-preview-wrap') ||
      target.closest('.ant-image-preview-operations') ||
      target.closest('.anticon') ||
      target.classList.contains('ant-image-img') ||
      target.classList.contains('ant-image-mask') ||
      target.classList.contains('ant-image-preview-close') ||
      target.classList.contains('anticon')
    ) {
      return;
    }
    
    if (editable && onItemClick) {
      const childItem = item.children?.[childIndex];
      if (childItem) {
        onItemClick({
          ...childItem,
          metadata: {
            ...childItem.metadata,
            parentDocId: item.metadata?.doc_id,
            parentIndex,
            childIndex,
          }
        } as unknown as RecallTestData, parentIndex);
      }
    }
  };

  // Get color class based on score
  const getScoreColorClass = (score: number): string => {
    const percentage = score * 100;
    if (percentage >= 90) {
      return 'rb:text-[#155EEF]';
    } else if (percentage >= 80) {
      return 'rb:text-[#369F21]';
    } else {
      return 'rb:text-[#FF5D34]';
    }
  };
  const handleDelete = (e: MouseEvent, item: RecallTestData, type?: 'child') => {
    e.preventDefault();
    e.stopPropagation();
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: type === 'child' ? `C-${item.metadata?.sort_id + 1}` : `chunk_${item.metadata?.sort_id}` }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteDocumentChunk(item.metadata.knowledge_id, item.metadata.document_id, item.metadata.doc_id)
          .then(() => {
            message.success(t('common.deleteSuccess'));
            refresh?.()
          })
      }
    })
    console.log('RecallTestData', item)
  }

  // Show skeleton when initial loading
  if (loading && data.length === 0) {
    return (
      <div className='rb:flex rb:flex-col'>
        <div className='rb:flex rb:items-center rb:justify-start rb:gap-2 rb:mb-4'>
          <span className='rb:text-lg rb:font-medium'>{t('knowledgeBase.recallResult')}</span>
        </div>
        <Skeleton active paragraph={{ rows: 3 }} />
        <Skeleton active paragraph={{ rows: 3 }} className='rb:mt-4' />
        <Skeleton active paragraph={{ rows: 3 }} className='rb:mt-4' />
      </div>
    );
  }

  if (data.length === 0 && showEmpty) {
    return (
      <NoData
        title={t('knowledgeBase.recallTestUnStart')}
        subTitle={t('knowledgeBase.recallTestUnStartSubTitle')}
      />
    );
  }

  if (data.length === 0) {
    return null;
  }

  const renderChild = (children: RecallTestData[], item: RecallTestData, index: number) => {
    if (children.length === 0) {
      return null;
    }
    const isExpanded = expandedChunks[`chunk_${item.metadata?.doc_id || index}`];
    return (
      <Flex vertical gap={8} className='rb:mt-3! rb:overflow-hidden'>
        <Flex 
          align="center"
          justify="space-between"
          className={clsx('rb:px-3! rb:py-2! rb:cursor-pointer', {
            'rb:bg-[#c8ceda33] rb:rounded-lg': !isExpanded
          })}
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
            setExpandedChunks(prev => {
              const key = `chunk_${item.metadata?.doc_id || index}`;
              const newExpanded = { ...prev };
              newExpanded[key] = !newExpanded[key];
              return newExpanded;
            });
          }}
        >
          <Flex align="center" gap={8}>
            <div
              className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/caret_right_outlined.svg')]", {
                'rb:rotate-90': isExpanded
              })}
            ></div>
            <span className='rb:text-[12px] rb:font-medium'>
              {children.length} {t('knowledgeBase.childSegmentation')}
            </span>
          </Flex>
          {editable &&
            <span className='rb:text-[12px] rb:text-[#155EEF]'
              onClick={(e) => {
                e.stopPropagation();
                handleInsert?.(item.metadata?.doc_id);
              }}
            >+ {t('common.add')}</span>
          }
        </Flex>
        {isExpanded && (
          <Flex vertical gap={8} className="rb:pl-3! rb:border-l-2 rb:border-l-[#155EEF]">
            {children?.map((child, childIndex) => (
              <Flex
                key={child.metadata?.doc_id || childIndex}
                className="rb:group rb:bg-[#c8ceda33] rb:hover:bg-[rgba(21,94,239,0.25)]"
              >
                <Flex
                  align="center"
                  className='rb:text-[12px] rb:px-1! rb:bg-[#c8ceda66] rb:font-medium rb:shrink-0 rb:group-hover:bg-[#155EEF] rb:group-hover:text-[#FFFFFF]'
                >
                  C-{child.metadata?.sort_id + 1}
                </Flex>
                <Flex
                  align="center"
                  className='rb:flex-1! rb:whitespace-pre-wrap'
                >
                  {renderTextContent(child.page_content)}
                </Flex>
                {editable && (
                  <Space>
                    <div
                      className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/edit.svg')]"
                      onClick={(e) => handleChildItemClick(e, item, childIndex, index)}
                    ></div>
                    <div
                      className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_hover.svg')]"
                      onClick={(e) => handleDelete(e, child, 'child')}
                    ></div>
                  </Space>
                )}
              </Flex>
            ))}
          </Flex>
        )}
      </Flex>
    )
  }

  const renderContent = () => (
    <div className='rb:flex rb:flex-col rb:mt-4'>
      {data.map((item, index) => {
        const score = item.metadata?.score ?? 1;
        const scorePercentage = score * 100;
        const colorClass = getScoreColorClass(score);
        const showScore = item.metadata?.score !== null && item.metadata?.score !== undefined;
        const children = item.children || [];
        return (
          <div
            key={`${item.metadata?.sort_id || index}-${index}`}
            className={`rb:flex rb:flex-col rb:mb-4 rb:rounded-xl rb:bg-[#F6F6F6] rb:p-4 rb:pt-2 rb:pb-3 rb:relative rb:group ${editable ? 'rb:cursor-pointer rb:transition-all hover:rb:border-[#155EEF] hover:rb:shadow-md' : ''}`}
            onClick={(e) => handleItemClick(e, item, index)}
          >
            {editable && (
              <div className='rb:absolute rb:top-2 rb:right-2 rb:opacity-0 rb:group-hover:rb:opacity-100 rb:transition-opacity'>
                <EditOutlined className='rb:text-[#155EEF] rb:text-base' />
              </div>
            )}
            <div className='rb:flex rb:items-center rb:justify-between'>
              {showScore && (
                <span className={`${colorClass} rb:text-xl rb:font-semibold`}>
                  {scorePercentage.toFixed(1)}% {t('knowledgeBase.similarity')}
                </span>
              )}
              <div className={`rb:flex rb:mt-2 rb:items-end rb:justify-end rb:gap-4 ${!showScore ? 'rb:w-full' : ''}`}>
                <span className='rb:text-gray-800'>
                  <FileOutlined /> {item.metadata?.file_name || '-'}
                </span>
                <span className='rb:text-gray-500 rb:text-xs rb:bg-[#DFDFDF] rb:px-1 rb:py-0.5 rb:rounded'>
                  chunk_{item.metadata?.sort_id || index}
                </span>
                <div
                  className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_hover.svg')]"
                  onClick={(e) => handleDelete(e, item)}
                ></div>
              </div>
            </div>
            <div className='rb:flex rb:text-left rb:px-4 rb:py-3 rb:bg-white rb:rounded-lg rb:mt-2'>
              <div className='rb:text-gray-800 rb:text-sm rb:whitespace-pre-wrap rb:wrap-break-word rb:w-full'>
                {(() => {
                  const qaContent = parseQAContent(item.page_content);
                  if (qaContent) {
                    const formattedContent = formatQAContent(qaContent.question, qaContent.answer);
                    return renderTextContent(formattedContent);
                  }
                  return children?.length > 0
                    ? <div className="rb:max-h-12.5 rb:overflow-hidden">
                      {renderTextContent(item.page_content)}
                    </div>
                    : renderTextContent(item.page_content);
                })()}
                {renderChild(children, item, index)}
              </div>
            </div>
            <Flex align="center" justify={item.metadata?.file_created_at ? 'space-between' : 'end'} className="rb:mt-3!">
              {item.metadata?.file_created_at && (
                <div className='rb:flex rb:items-center rb:justify-start'>
                  <span className='rb:text-gray-500 rb:text-xs'>
                    <FieldTimeOutlined /> {formatDateTime(item.metadata.file_created_at)}
                  </span>
                </div>
              )}
              <Space align="center" className='rb:text-gray-500 rb:text-xs' onClick={() => handleCopy?.(item.metadata?.doc_id)}>
                ID: {item.metadata?.doc_id}
                <span
                  className="rb:cursor-pointer rb:inline-block rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/copy_dark.svg')]"
                ></span>
              </Space>
            </Flex>
          </div>
        );
      })}
      {loading && (
        <div className='rb:mb-4'>
          <Skeleton active paragraph={{ rows: 3 }} />
        </div>
      )}
    </div>
  );

  // If loadMore and hasMore are provided, use InfiniteScroll
  if (loadMore && hasMore !== undefined) {
    return (
      <div className='rb:flex rb:h-full rb:flex-col'>
        <div className='rb:flex rb:items-center rb:justify-start rb:gap-2'>
          <span className='rb:text-lg rb:font-medium'>{t('knowledgeBase.recallResult')}</span>
          <span className='rb:text-gray-500 rb:text-xs rb:pt-0.5'>
            (<span className='rb:text-[#155EEF]'>{total || data.length}</span> results)
          </span>
        </div>
        <InfiniteScroll
          dataLength={data.length}
          next={loadMore}
          hasMore={hasMore}
          loader={<Skeleton active paragraph={{ rows: 3 }} className='rb:mt-4' />}
          scrollableTarget={scrollableTarget}
        >
          {renderContent()}
        </InfiniteScroll>
      </div>
    );
  }


  // Otherwise use normal rendering
  return (
    <div className='rb:flex rb:flex-col'>
      <div className='rb:flex rb:items-center rb:justify-start rb:gap-2'>
        <span className='rb:text-lg rb:font-medium'>{t('knowledgeBase.recallResult')}</span>
        <span className='rb:text-gray-500 rb:text-xs rb:pt-0.5'>
          (<span className='rb:text-[#155EEF]'>{data.length}</span> results)
        </span>
      </div>
      {renderContent()}
    </div>
  );
};

export default RecallTestResult;
