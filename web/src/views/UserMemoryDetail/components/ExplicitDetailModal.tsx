import { forwardRef, useImperativeHandle, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom'
import { Descriptions, Skeleton } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal'
import { getExplicitMemoryDetails } from '@/api/memory'
import { formatDateTime } from '@/utils/format'
import type { ExplicitDetailModalRef, EpisodicMemory, SemanticMemory } from '../pages/ExplicitDetail'


interface Data {
  memory_type: 'episodic' | 'semantic';
  title: string;
  content: string;
  emotion: string;
  created_at: number;
  
  name: string;
  core_definition: string;
  detailed_notes: string;
}
const ExplicitDetailModal = forwardRef<ExplicitDetailModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const { id } = useParams()
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<Data>({} as Data)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    setData({} as Data)
  };

  const handleOpen = (vo: EpisodicMemory | SemanticMemory) => {
    setLoading(true)
    getExplicitMemoryDetails({
      end_user_id: id as string,
      memory_id: vo.id
    })
      .then(res => {
        setVisible(true);
        setData(res as Data)
      })
      .finally(() => {
        setLoading(false)
      })
  };

  const getEmotionColor = (emotionType: string) => {
    const colors: Record<string, string> = {
      joy: '#52c41a',
      anger: '#ff4d4f',
      sadness: '#1890ff',
      fear: '#fa8c16',
      neutral: '#8c8c8c',
      surprise: '#722ed1'
    }
    return colors[emotionType] || '#8c8c8c'
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));
  return (
    <RbModal
      title={data.name || data.title}
      open={visible}
      footer={null}
      onCancel={handleClose}
    >
      {loading ? <Skeleton active />
        : <Descriptions column={data.memory_type === 'semantic' ? 1 : 2} classNames={{ label: 'rb:w-20' }}>
          {data.emotion && <Descriptions.Item label={t('explicitDetail.emotion')}>
            <div className="rb:flex rb:items-center rb:gap-2">
              <div className="rb:w-3 rb:h-3 rb:rounded-full" style={{ backgroundColor: getEmotionColor(data.emotion) }}></div>
              <span className="rb:text-gray-600">{t(`statementDetail.${data.emotion || 'neutral'}`)}</span>
            </div>
          </Descriptions.Item>}
          {data.core_definition && <Descriptions.Item label={t('explicitDetail.core_definition')}>
            {data.core_definition}
          </Descriptions.Item>}
          {data.detailed_notes && <Descriptions.Item label={t('explicitDetail.detailed_notes')}>
            {data.detailed_notes}
          </Descriptions.Item>}
          {data.created_at && <Descriptions.Item label={t('explicitDetail.created_at')}>
            {formatDateTime(data.created_at)}
          </Descriptions.Item>}
          {data.content && <Descriptions.Item span="filled" label={t('explicitDetail.content')}>
            {data.content}
          </Descriptions.Item>}
        </Descriptions>
      }
    </RbModal>
  );
});

export default ExplicitDetailModal;