import { useEffect, useState, type FC } from 'react';
import { useParams, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Popover, Flex, Spin } from 'antd';

import type { V1KnowledgeListItem } from '@/views/KnowledgeBase/typesV1';
import RecallTest from '../components/RecallTest';
import InfoPanel, { type InfoItem } from '../components/InfoPanel';
import shareUserIcon from '@/assets/images/knowledgeBase/share-user.png';
import timestampIcon from '@/assets/images/knowledgeBase/timestamp.png';
//
import kbNameIcon from '@/assets/images/knowledgeBase/kb-name.png';
import kbDataIcon from '@/assets/images/knowledgeBase/kb-data.png';
import kbSizeIcon from '@/assets/images/knowledgeBase/kb-size.png';
import kbModelIcon from '@/assets/images/knowledgeBase/kb-model.png';
import NoData from '@/assets/images/knowledgeBase/noData.png'

import kbHistoryIcon from '@/assets/images/knowledgeBase/kb-history.png';
import { getKnowledgeBaseDetailV1 } from '@/api/knowledgeBaseV1';
import { formatDateTime } from '@/utils/format';
import { useBreadcrumbManager, type BreadcrumbItem } from '@/hooks/useBreadcrumbManager';
import Empty from '@/components/Empty'

const Share: FC = () => {
  const { t } = useTranslation();
  const params = useParams<{ knowledgeBaseId: string }>();
  const location = useLocation();
  const knowledgeBaseId = params.knowledgeBaseId;
  const [loading, setLoading] = useState(false);
  const [knowledgeBase, setKnowledgeBase] = useState<V1KnowledgeListItem | null>(null);
  const [infoItems, setInfoItems] = useState<InfoItem[]>([]);
  const [knowledgeBaseFolderPath, setKnowledgeBaseFolderPath] = useState<BreadcrumbItem[]>([]);
  const { updateBreadcrumbs } = useBreadcrumbManager({
    breadcrumbType: 'detail'
  });

  // Update breadcrumbs
  useEffect(() => {
    if (knowledgeBase) {
      updateBreadcrumbs({
        knowledgeBaseFolderPath,
        knowledgeBase: {
          id: knowledgeBase.id,
          name: knowledgeBase.name,
          type: 'knowledgeBase'
        },
        documentFolderPath: [],
      });
    }
  }, [knowledgeBase, knowledgeBaseFolderPath, updateBreadcrumbs]);

  // Listen to location state changes
  useEffect(() => {
    const state = location.state as { 
      fromKnowledgeBaseList?: boolean;
      knowledgeBaseFolderPath?: BreadcrumbItem[];
    } | null;
    
    // If navigated from knowledge base list page, set knowledge base folder path
    if (state?.fromKnowledgeBaseList && state?.knowledgeBaseFolderPath) {
      setKnowledgeBaseFolderPath(state.knowledgeBaseFolderPath);
    }
  }, [location.state]);
  const formatInfoItems = (data: V1KnowledgeListItem): InfoItem[] => {
    const multimodalEnabled = data.processing_configuration?.multimodal?.enabled === true;
    const items: InfoItem[] = [
      { 
        key: 'name',
        label: t('knowledgeBase.knowledgeBase') + ' ' + t('knowledgeBase.name'),
        value: data.name ?? '-',
        icon: kbNameIcon,
      },
      {
        key: 'doc_num',
        label: t('knowledgeBase.doc_num'),
        value: data.doc_num ?? 0,
        icon: kbDataIcon,
      },
      {
        key: 'chunk_num',
        label: t('knowledgeBase.chunk_num'),
        value: data.chunk_num ?? 0,
        icon: kbSizeIcon,
      },
      {
        key: 'embedding_id',
        label: t('knowledgeBase.embedding_id') + ' ' + 'model',
        value: data.embedding?.name ?? '-',
        icon: kbModelIcon,
      },
      {
        key: 'llm_id',
        label: t('knowledgeBase.llm_id') + ' ' + 'model',
        value: data.llm?.name ?? '-',
        icon: kbModelIcon,
      },
      ...(multimodalEnabled
        ? [
            {
              key: 'image2text_id',
              label: t('knowledgeBase.image2text_id') + ' ' + 'model',
              value: data.image2text?.name ?? '-',
              icon: kbModelIcon,
            },
            {
              key: 'audio2text_id',
              label: t('knowledgeBase.audio2text_id') + ' ' + 'model',
              value: data.audio2text?.name ?? '-',
              icon: kbModelIcon,
            },
            {
              key: 'video2text_id',
              label: t('knowledgeBase.video2text_id') + ' ' + 'model',
              value: data.video2text?.name ?? '-',
              icon: kbModelIcon,
            },
          ]
        : []),
      {
        key: 'updated_at',
        label: t('knowledgeBase.last_at'),
        value: formatDateTime(data.updated_at, 'YYYY-MM-DD HH:mm:ss'),
        icon: kbHistoryIcon,
      },
    ];
    
    return items.filter((item) => {
      return item.value !== null && item.value !== undefined && item.value !== '';
    });
  }

  useEffect(() => {
    if (!knowledgeBaseId) return
    fetchKnowledgeBaseDetail(knowledgeBaseId)
  }, [knowledgeBaseId])
  const fetchKnowledgeBaseDetail = (id: string) => {
    setLoading(true);
    getKnowledgeBaseDetailV1(id)
      .then((detail) => {
        setKnowledgeBase(detail);
        setInfoItems(formatInfoItems(detail));
      })
      .finally(() => {
        setLoading(false);
      });
  };



  // const handleBack = () => {
  //   navigate('/knowledge-base');
  // };

  if (loading) {
    return (
      <Flex align="center" justify="center" className="rb:h-full">
        <Spin tip={t('common.loading')}><div className="rb:size-32" /></Spin>
      </Flex>
    )
  }

  if (!knowledgeBase) {
    return (
      <Empty
        url={NoData}
        subTitle={t('knowledgeBase.knowledgeBaseNotFound')}
        size={[260, 180]}
        className="rb:h-full!"
      />
    );
  }

  return (
    <Flex vertical className="rb:h-full rb:max-h-full rb:overflow-hidden">
      <Flex align="center" gap={8} className="rb:w-full rb:mb-2!">
        <h1 className="rb:text-xl rb:font-bold">{knowledgeBase.name}
          <span className='rb:text-gray-500 rb:text-sm rb:ml-2 rb:font-normal'>(ID: {knowledgeBase.id})</span></h1>
        
        {/* <p className="rb:text-gray-600 rb:mt-2">{knowledgeBase.description || t('knowledgeBase.noDescription')}</p> */}
        <span className='rb:text-white rb:text-xs rb:bg-blue-500 rb:px-1 rb:py-0.5 rb:rounded'>{knowledgeBase.permission_id}</span>
        <Popover
          content={
            <div className="rb:max-h-75 rb:overflow-y-auto">
              <InfoPanel title={t('knowledgeBase.knowledgeBaseInfo')} items={infoItems} />
            </div>
          }
          trigger="hover"
          placement="bottomLeft"
          arrow={false}
        >
          <span 
            className='rb:border rb:border-primary-600 rb:px-1 rb:py-0.5 rb:rounded rb:text-primary-600 rb:text-xs rb:cursor-pointer'
          >{t('knowledgeBase.viewBasicInfo')}</span>
        </Popover>
      </Flex>
      <Flex align="center" gap={8} className="rb:w-full rb:mb-3!">
        <img src={shareUserIcon} className='rb:size-4 rb:ml-2' />
          <span className='rb:text-gray-500 rb:text-xs'>{knowledgeBase.created_by}</span>
          <img src={timestampIcon} className='rb:size-4 rb:ml-2' />
          <span className='rb:text-gray-500 rb:text-xs'>{formatDateTime(knowledgeBase.created_at)}</span>
      </Flex>
      <Flex gap={16} className="rb:flex-1 rb:min-h-0">
        <Flex vertical className="rb:flex-1 rb:p-4! rb-border rb:bg-white rb:rounded-xl rb:overflow-hidden">
          <Flex vertical gap={8} className='rb:txt-left rb:mb-3! rb:shrink-0'>
            <h1 className="rb:text-[16px] rb:font-bold">{t('knowledgeBase.knowledgeBase')} {t('knowledgeBase.recallTest')}</h1>
            <span className='rb:text-gray-500 rb:text-xs'>{t('knowledgeBase.recallTestDescription')}</span>
          </Flex>
          <div className='rb:flex-1 rb:min-h-0'>
            <RecallTest />
          </div>
        </Flex>
        {/* <div className='rb:w-80 rb:border rb:overflow-y-auto rb:border-[#DFE4ED] rb:bg-white rb:rounded-xl rb:p-4'>
         
        </div> */}
      </Flex>

    </Flex>
  );
};

export default Share;

