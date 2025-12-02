import { useEffect, useState, type FC } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Button } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';

import { request } from '@/utils/request';
import type { KnowledgeBase } from './types';

const Datasets: FC = () => {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);

  useEffect(() => {
    if (id) {
      fetchKnowledgeBaseDetail(id);
    }
  }, [id]);

  const fetchKnowledgeBaseDetail = (knowledgeBaseId: string) => {
    setLoading(true);
    request.get(`/knowledgeBase/${knowledgeBaseId}`)
      .then((res: any) => {
        setKnowledgeBase(res.data || res);
      })
      .finally(() => {
        setLoading(false);
      });
  };

  const handleBack = () => {
    navigate('/knowledge-base');
  };

  if (loading) {
    return <div>加载中...</div>;
  }

  if (!knowledgeBase) {
    return <div>知识库不存在</div>;
  }

  return (
    <div className="rb:p-6">
      <div className="rb:mb-4">
        <Button 
          icon={<ArrowLeftOutlined />} 
          onClick={handleBack}
        >
          返回
        </Button>
      </div>
      
      <div className="rb:mb-4">
        <h1 className="rb:text-2xl rb:font-bold">{knowledgeBase.name}</h1>
        <p className="rb:text-gray-600 rb:mt-2">{knowledgeBase.description || t('knowledgeBase.noDescription')}</p>
      </div>

      <div className="rb:bg-white rb:p-4 rb:rounded">
        <h2 className="rb:text-lg rb:font-semibold rb:mb-4">{t('knowledgeBase.datasets')}</h2>
        {/* TODO: 添加数据集列表 */}
        <div>{t('knowledgeBase.noDataSets')}</div>
      </div>
    </div>
  );
};

export default Datasets;

