/*
 * @Description: 滚动列表
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-18 16:19:58
 * @LastEditors: yujiangping
 * @LastEditTime: 2025-12-22 13:47:53
 */
import { FileOutlined, FieldTimeOutlined, EditOutlined } from '@ant-design/icons';
import { Skeleton } from 'antd';
import { useTranslation } from 'react-i18next';
import type { RecallTestData } from '@/views/KnowledgeBase/types';
import { NoData } from './noData';
import { formatDateTime } from '@/utils/format';
import InfiniteScroll from 'react-infinite-scroll-component';
import RbMarkdown from '@/components/Markdown';

interface RecallTestResultProps {
  data: RecallTestData[];
  showEmpty?: boolean;
  hasMore?: boolean;
  loadMore?: () => void;
  loading?: boolean;
  scrollableTarget?: string;
  editable?: boolean; // 是否可编辑
  onItemClick?: (item: RecallTestData, index: number) => void; // 点击项的回调
  parserMode?: number; // 解析模式，1 表示 QA 格式
}

const RecallTestResult = ({ 
  data, 
  showEmpty = true,
  hasMore = false,
  loadMore,
  loading = false,
  scrollableTarget,
  editable = false,
  onItemClick,
  parserMode = 0,
}: RecallTestResultProps) => {
  const { t } = useTranslation();

  // 解析 QA 格式内容
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

  // 格式化 QA 内容为显示格式
  const formatQAContent = (question: string, answer: string) => {
    return `**${t('knowledgeBase.question')}:** ${question}\n**${t('knowledgeBase.answer')}:** ${answer}`;
  };

  const handleItemClick = (e: React.MouseEvent, item: RecallTestData, index: number) => {
    // 检查点击的是否是图片或图片相关元素
    const target = e.target as HTMLElement;
    
    // 检查是否点击了图片本身、图片的容器、预览层、关闭按钮或 SVG 图标
    if (
      target.tagName === 'IMG' || 
      target.tagName === 'SVG' || // SVG 图标
      target.tagName === 'PATH' || // SVG 路径
      target.closest('.ant-image') ||
      target.closest('.ant-image-preview') ||
      target.closest('.ant-image-preview-wrap') ||
      target.closest('.ant-image-preview-operations') ||
      target.closest('.anticon') || // Ant Design 图标
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

  // 根据分数获取颜色类名
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

  const renderContent = () => (
    <div className='rb:flex rb:flex-col rb:mt-4'>
      {data.map((item, index) => {
        const score = item.metadata?.score ?? 1;
        const scorePercentage = score * 100;
        const colorClass = getScoreColorClass(score);
        const showScore = item.metadata?.score !== null && item.metadata?.score !== undefined;
        
        return (
          <div
            key={`${item.metadata?.sort_id || index}-${index}`}
            className={`rb:flex rb:flex-col rb:mb-4 rb:rounded-lg rb:border rb:border-[#DFE4ED] rb:bg-[#FBFDFF] rb:p-4 rb:pt-2 rb:pb-3 rb:relative rb:group ${editable ? 'rb:cursor-pointer rb:transition-all hover:rb:border-[#155EEF] hover:rb:shadow-md' : ''}`}
            onClick={(e) => handleItemClick(e, item, index)}
          >
            {editable && (
              <div className='rb:absolute rb:top-2 rb:right-2 rb:opacity-0 group-hover:rb:opacity-100 rb:transition-opacity'>
                <EditOutlined className='rb:text-[#155EEF] rb:text-base' />
              </div>
            )}
            <div className='rb:flex rb:items-center rb:justify-between'>
              {showScore && (
                <span className={`${colorClass} rb:text-xl rb:font-semibold`}>
                  {scorePercentage.toFixed(1)}% {t('knowledgeBase.similarity')}
                </span>
              )}
              <div className={`rb:flex rb:mt-2 rb:flex-col rb:items-end rb:justify-end rb:gap-1 ${!showScore ? 'rb:w-full' : ''}`}>
                <span className='rb:text-gray-800'>
                  <FileOutlined /> {item.metadata?.file_name || '-'}
                </span>
                <span className='rb:text-gray-500 rb:text-xs rb:bg-[#F0F3F8] rb:px-1 rb:py-[2px] rb:rounded'>
                  chunk_{item.metadata?.sort_id || index}
                </span>
              </div>
            </div>
            <div className='rb:flex rb:text-left rb:px-4 rb:py-3 rb:bg-[#F0F3F8] rb:rounded-lg rb:mt-2'>
              <div className='rb:text-gray-800 rb:text-sm rb:whitespace-pre-wrap rb:break-words rb:w-full'>
                {(() => {
                  const qaContent = parseQAContent(item.page_content);
                  if (qaContent) {
                    const formattedContent = formatQAContent(qaContent.question, qaContent.answer);
                    return <RbMarkdown content={formattedContent} showHtmlComments={true} />;
                  }
                  return <RbMarkdown content={item.page_content} showHtmlComments={true} />;
                })()}
              </div>
            </div>
            {item.metadata?.file_created_at && (
              <div className='rb:flex rb:items-center rb:justify-start rb:mt-3'>
                <span className='rb:text-gray-500 rb:text-xs'>
                  <FieldTimeOutlined /> {formatDateTime(item.metadata.file_created_at)}
                </span>
              </div>
            )}
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

  // 如果提供了 loadMore 和 hasMore，使用 InfiniteScroll
  if (loadMore && hasMore !== undefined) {
    return (
      <div className='rb:flex rb:h-full rb:flex-col'>
        <div className='rb:flex rb:items-center rb:justify-start rb:gap-2'>
          <span className='rb:text-lg rb:font-medium'>{t('knowledgeBase.recallResult')}</span>
          <span className='rb:text-gray-500 rb:text-xs rb:pt-[2px]'>
            (<span className='rb:text-[#155EEF]'>{data.length}</span> results)
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

  // 否则使用普通渲染
  return (
    <div className='rb:flex rb:flex-col'>
      <div className='rb:flex rb:items-center rb:justify-start rb:gap-2'>
        <span className='rb:text-lg rb:font-medium'>{t('knowledgeBase.recallResult')}</span>
        <span className='rb:text-gray-500 rb:text-xs rb:pt-[2px]'>
          (<span className='rb:text-[#155EEF]'>{data.length}</span> results)
        </span>
      </div>
      {renderContent()}
    </div>
  );
};

export default RecallTestResult;
