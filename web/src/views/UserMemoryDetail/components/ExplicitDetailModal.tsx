/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:33:16 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:33:16 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { useParams } from 'react-router-dom'
import { Descriptions, Skeleton } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal'
import { getExplicitMemoryDetails } from '@/api/memory'
import { formatDateTime } from '@/utils/format'
import type { ExplicitDetailModalRef, EpisodicMemory, SemanticMemory } from '../pages/ExplicitDetail'


/**
 * Explicit memory detail data structure
 * @property {string} memory_type - Type of memory (episodic or semantic)
 * @property {string} title - Memory title
 * @property {string} content - Memory content
 * @property {string} emotion - Associated emotion
 * @property {number} created_at - Creation timestamp
 * @property {string} name - Memory name
 * @property {string} core_definition - Core definition for semantic memory
 * @property {string} detailed_notes - Detailed notes
 */
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

/**
 * ExplicitDetailModal Component
 * Modal dialog for displaying detailed information about explicit memories
 * Shows different fields based on memory type (episodic vs semantic)
 */
const ExplicitDetailModal = forwardRef<ExplicitDetailModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const { id } = useParams()
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<Data>({} as Data)

  /**
   * Close modal and reset data
   */
  const handleClose = () => {
    setVisible(false);
    setData({} as Data)
  };

  /**
   * Open modal and load memory details
   * @param {EpisodicMemory | SemanticMemory} vo - Memory object
   */
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

  /**
   * Get color for emotion type
   * @param {string} emotionType - Emotion type
   * @returns {string} Color hex code
   */
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

  /**
   * Expose methods to parent component
   */
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
        : <Descriptions column={1} classNames={{ label: 'rb:w-20' }}>
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
          {data.content && <Descriptions.Item label={t('explicitDetail.content')}>
            {data.content}
          </Descriptions.Item>}
        </Descriptions>
      }
    </RbModal>
  );
});

export default ExplicitDetailModal;