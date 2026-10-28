/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-12-30 15:07:37
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-01-05 20:28:51
 */
import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next';
import { Button, Flex } from 'antd';

import KnowledgeGraph, { type KnowledgeGraphResponse } from './KnowledgeGraph'
import { getKnowledgeGraph } from '@/api/knowledgeBase';
import { type KnowledgeBase } from '../types';
import Empty from '@/components/Empty';
interface KnowledgeGraphCardProps {
  knowledgeBase?: KnowledgeBase;
  onRebuildGraph?: () => void; // Callback function to rebuild graph
}

const KnowledgeGraphCard: React.FC<KnowledgeGraphCardProps> = ({ knowledgeBase, onRebuildGraph }) => {
  const { t } = useTranslation();
  const [data, setData] = useState<KnowledgeGraphResponse | undefined>()
  const [loading, setLoading] = useState(true)
  const handleRebuildGraph = () => {
    // Call parent component's callback to open CreateModal with rebuild flag
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
        // Check if res.graph is empty object or doesn't exist
        const graphResponse = res as KnowledgeGraphResponse;
        if (!graphResponse || !graphResponse.graph || Object.keys(graphResponse.graph).length === 0) {
          setData(undefined) // Set to undefined to show empty state
        } else {
          setData(graphResponse)
        }
      } catch (error) {
        console.error('Failed to fetch knowledge graph data:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [knowledgeBase?.id])

  const title = knowledgeBase?.parser_config?.graphrag?.use_graphrag
    ? knowledgeBase?.parser_config?.graphrag?.scene_name
    : null
  return (
    <Flex vertical className='rb:w-full rb:h-full!'>
      <Flex align="center" justify="space-between" className='rb:w-full rb:py-3! rb:px-2!'>
        <div>
          <div className='rb:w-full rb:text-lg rb:font-medium rb:text-gray-800 rb:leading-6'>
            {title}
            <span className={title ? "rb:text-gray-600" : ''}>
              {title ? ' - ' : null}{t('knowledgeBase.graphTitle')}
            </span>
          </div>
          <div className='rb:w-full rb:text-xs rb:text-gray-600 rb:leading-4 rb:mt-2'>
            {t('knowledgeBase.graphTips')}
          </div>
        </div>
        <Button type="primary" onClick={() => handleRebuildGraph()}>
          {t('knowledgeBase.rebuildGraph')}
        </Button>
      </Flex>
      <div className='rb:flex-1 rb:overflow-hidden!'>
        {knowledgeBase?.parser_config?.graphrag?.use_graphrag
          ? <KnowledgeGraph data={data} loading={loading} />
          : <Empty title={t('knowledgeBase.graphEmpty')} size={88} className="rb:h-full!" />
        }
      </div>
    </Flex>
  )
}

export default KnowledgeGraphCard