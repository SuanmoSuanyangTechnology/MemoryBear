import { useEffect, useState, useRef, type FC } from 'react';
import { useParams, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { KnowledgeBaseListItem, RecallTestDrawerRef } from '@/views/KnowledgeBase/types';
import RecallTest from '../components/RecallTest';
import InfoPanel, { type InfoItem } from '../components/InfoPanel';
import shareUserIcon from '@/assets/images/knowledgeBase/share-user.png';
import timestampIcon from '@/assets/images/knowledgeBase/timestamp.png';
//
import kbNameIcon from '@/assets/images/knowledgeBase/kb-name.png';
import kbDataIcon from '@/assets/images/knowledgeBase/kb-data.png';
import kbSizeIcon from '@/assets/images/knowledgeBase/kb-size.png';
import kbModelIcon from '@/assets/images/knowledgeBase/kb-model.png';

import kbHistoryIcon from '@/assets/images/knowledgeBase/kb-history.png';
import { getKnowledgeBaseDetail } from '@/api/knowledgeBase';
import { formatDateTime } from '@/utils/format';
import { useBreadcrumbManager, type BreadcrumbItem } from '@/hooks/useBreadcrumbManager';

const Share: FC = () => {
  const { t } = useTranslation();
  const params = useParams<{ knowledgeBaseId: string }>();
  const location = useLocation();
  const knowledgeBaseId = params.knowledgeBaseId;
  const [loading, setLoading] = useState(false);
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBaseListItem | null>(null);
  const recallTestRef = useRef<RecallTestDrawerRef>(null);
  const [infoItems, setInfoItems] = useState<InfoItem[]>([]);
  const [knowledgeBaseFolderPath, setKnowledgeBaseFolderPath] = useState<BreadcrumbItem[]>([]);
  
  const { updateBreadcrumbs } = useBreadcrumbManager({
    breadcrumbType: 'detail'
  });
  useEffect(() => {
    console.log('Share.tsx - useParams result:', params);
    console.log('Share.tsx - knowledgeBaseId:', knowledgeBaseId);
    console.log('Share.tsx - typeof knowledgeBaseId:', typeof knowledgeBaseId);
    
    if (knowledgeBaseId) {
      fetchKnowledgeBaseDetail(knowledgeBaseId);
      // 打开召回测试组件
      setTimeout(() => {
        console.log('Share.tsx - calling handleOpen with:', knowledgeBaseId);
        recallTestRef.current?.handleOpen(knowledgeBaseId);
      }, 100);
    } else {
      console.warn('Share.tsx - knowledgeBaseId is undefined or empty');
    }
  }, [knowledgeBaseId]);

  // 更新面包屑
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

  // 监听 location state 变化
  useEffect(() => {
    const state = location.state as { 
      fromKnowledgeBaseList?: boolean;
      knowledgeBaseFolderPath?: BreadcrumbItem[];
    } | null;
    
    // 如果是从知识库列表页跳转过来的，设置知识库文件夹路径
    if (state?.fromKnowledgeBaseList && state?.knowledgeBaseFolderPath) {
      setKnowledgeBaseFolderPath(state.knowledgeBaseFolderPath);
    }
  }, [location.state]);
  const formatInfoItems = (data: KnowledgeBaseListItem): InfoItem[] => {
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
      {
        key: 'image2text_id',
        label: t('knowledgeBase.image2text_id') + ' ' + 'model',
        value: data.image2text?.name ?? '-',
        icon: kbModelIcon,
      },
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
  const fetchKnowledgeBaseDetail = (id: string) => {
    setLoading(true);
    getKnowledgeBaseDetail(id)
      .then((res: any) => {
        const data = res.data || res;
        setKnowledgeBase(data);
        setInfoItems(formatInfoItems(data));
      })
      .finally(() => {
        setLoading(false);
      });
  };



  // const handleBack = () => {
  //   navigate('/knowledge-base');
  // };

  if (loading) {
    return <div>加载中...</div>;
  }

  if (!knowledgeBase) {
    return <div>知识库不存在</div>;
  }

  return (
    <div className="rb:flex rb:flex-col rb:h-full rb:max-h-full rb:overflow-hidden">
      
      <div className="rb:flex rb:w-full rb:items-center rb:mb-2 rb:gap-2">
        <h1 className="rb:text-xl rb:font-bold">{knowledgeBase.name}
          <span className='rb:text-gray-500 rb:text-sm rb:ml-2 rb:font-normal'>(ID: {knowledgeBase.id})</span></h1>
        
        {/* <p className="rb:text-gray-600 rb:mt-2">{knowledgeBase.description || t('knowledgeBase.noDescription')}</p> */}
        <span className='rb:text-gray-800 rb:text-xs rb:border rb:border-[#369F21] rb:bg-[rgba(54,159,33,0.2)] rb:px-1 rb:py-[2px] rb:rounded'>{knowledgeBase.permission_id}</span>
      </div>
      <div className="rb:flex rb:w-full rb:items-center rb:mb-5 rb:gap-2">
          <img src={shareUserIcon} className='rb:size-4 rb:ml-2' />
          <span className='rb:text-gray-500 rb:text-xs'>{knowledgeBase.created_by}</span>
          <img src={timestampIcon} className='rb:size-4 rb:ml-2' />
          <span className='rb:text-gray-500 rb:text-xs'>{formatDateTime(knowledgeBase.created_at)}</span>
      </div>
      <div className="rb:flex rb:flex-1 rb:gap-4 rb:min-h-0">
        <div className="rb:flex-1 rb:p-4 rb:border rb:flex rb:flex-col rb:border-[#DFE4ED] rb:bg-white rb:rounded-xl rb:overflow-hidden">
          <div className='rb:flex rb:flex-col rb:txt-left rb:mb-5 rb:gap-2 rb:flex-shrink-0'>
            <h1 className="rb:text-lg rb:font-bold">{t('knowledgeBase.knowledgeBase')} {t('knowledgeBase.recallTest')}</h1>
            <span className='rb:text-gray-500 rb:text-xs'>{t('knowledgeBase.recallTestDescription')}</span>
          </div>
          <div className='rb:flex-1 rb:min-h-0'>
            <RecallTest  ref={recallTestRef} />
          </div>
        </div>
        <div className='rb:w-80 rb:border rb:overflow-y-auto rb:border-[#DFE4ED] rb:bg-white rb:rounded-xl rb:p-4'>
          <InfoPanel 
            title={t('knowledgeBase.knowledgeBaseInfo')} 
            items={infoItems}
          />
        </div>
      </div>


    </div>
  );
};

export default Share;

