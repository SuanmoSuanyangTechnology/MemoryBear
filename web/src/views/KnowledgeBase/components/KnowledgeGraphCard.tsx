/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-12-30 15:07:37
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-01-04 20:15:12
 */
import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next';
import { Row } from 'antd'
import KnowledgeGraph, { type KnowledgeGraphResponse } from './KnowledgeGraph'
import { getKnowledgeGraph } from '@/api/knowledgeBase';

interface KnowledgeGraphCardProps {
  knowledgeBaseId?: string;
}

const KnowledgeGraphCard: React.FC<KnowledgeGraphCardProps> = ({ knowledgeBaseId }) => {
  const { t } = useTranslation();
  const [data, setData] = useState<KnowledgeGraphResponse | undefined>()
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      if (!knowledgeBaseId) {
        setLoading(false)
        return
      }
      
      setLoading(true)
      try {
        const res = await getKnowledgeGraph(knowledgeBaseId)
        setData(res as KnowledgeGraphResponse)
      } catch (error) {
        console.error('获取知识图谱数据失败:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [knowledgeBaseId])

  return (
    <div className='rb:flex rb:flex-col'>
      <div className='rb:flex rb:flex-col rb:p-4'>
          <div className='rb:w-full rb:text-lg rb:font-medium rb:text-[#212332] rb:leading-6'>
             {t('knowledgeBase.graphTitle')}
          </div>
          <div className='rb:w-full rb:text-xs rb:text-[#5B6167] rb:leading-4 rb:mt-2'>
            {t('knowledgeBase.graphTips')}
          </div>
          <div className='rb:flex rb:items-center rb:justify-between'>
            
          </div>
      </div>
      <div className='rb:p-4'>
         <KnowledgeGraph data={data} loading={loading} />
      </div>
      
    </div>
  )
}

export default KnowledgeGraphCard