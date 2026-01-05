/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-12-30 15:07:37
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-01-05 16:18:53
 */
import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next';
import { Button } from 'antd';
import KnowledgeGraph, { type KnowledgeGraphResponse } from './KnowledgeGraph'
import { getKnowledgeGraph } from '@/api/knowledgeBase';
import { type KnowledgeBase } from '../types';
import Empty from '@/components/Empty';
interface KnowledgeGraphCardProps {
  knowledgeBase?: KnowledgeBase;
  onRebuildGraph?: () => void; // 添加重建图谱的回调函数
}

const KnowledgeGraphCard: React.FC<KnowledgeGraphCardProps> = ({ knowledgeBase, onRebuildGraph }) => {
  const { t } = useTranslation();
  const [data, setData] = useState<KnowledgeGraphResponse | undefined>()
  const [loading, setLoading] = useState(true)
  const handleRebuildGraph = () => {
    // 调用父组件传递的回调函数来打开CreateModal并传递重建标识
    if (onRebuildGraph) {
      onRebuildGraph();
    }
  }
  useEffect(() => {
    const fetchData = async () => {
      if (!knowledgeBase?.id) {
        setLoading(false)
        return
      }
      
      setLoading(true)
      try {
        const res = await getKnowledgeGraph(knowledgeBase?.id)
        setData(res as KnowledgeGraphResponse)
      } catch (error) {
        console.error('获取知识图谱数据失败:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [knowledgeBase?.id])

  return (
    <div className='rb:flex rb:w-full rb:flex-col'>
      <div className='rb:flex rb:w-full rb:flex-col rb:p-4'>
          <div className='rb:w-full rb:text-lg rb:font-medium rb:text-[#212332] rb:leading-6'>
             {t('knowledgeBase.graphTitle')}
          </div>
          <div className='rb:w-full rb:text-xs rb:text-[#5B6167] rb:leading-4 rb:mt-2'>
            {t('knowledgeBase.graphTips')}
          </div>
          <div className='rb:flex rb:w-full rb:items-center rb:justify-between rb:mt-4'>
            <span className='rb:text-base rb:font-medium rb:text-[#212332]'>
              {knowledgeBase?.parser_config?.graphrag?.scene_name}
            </span>
            <Button type="primary" onClick={() => handleRebuildGraph()}>
                {t('knowledgeBase.rebuildGraph')}
            </Button>
          </div>
      </div>
      <div className='rb:p-4 rb:pt-0'>
        {knowledgeBase?.parser_config?.graphrag?.use_graphrag ? (<KnowledgeGraph data={data} loading={loading} />) : <Empty />}
      </div>
      
    </div>
  )
}

export default KnowledgeGraphCard