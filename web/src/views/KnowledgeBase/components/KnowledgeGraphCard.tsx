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

  return (
    <Flex vertical className='rb:w-full'>
      <Flex align="center" justify="space-between" className='rb:w-full rb:px-4! rb:pb-4!'>
        <div>
          <div className='rb:w-full rb:text-lg rb:font-medium rb:text-[#212332] rb:leading-6'>
              {t('knowledgeBase.graphTitle')}
          </div>
          <div className='rb:w-full rb:text-xs rb:text-[#5B6167] rb:leading-4 rb:mt-2'>
            {t('knowledgeBase.graphTips')}
          </div>
        </div>
        <Button type="primary" onClick={() => handleRebuildGraph()}>
          {t('knowledgeBase.rebuildGraph')}
        </Button>
      </Flex>
      <div className='rb:px-4 rb:pb-4'>
        {knowledgeBase?.parser_config?.graphrag?.use_graphrag ? 
          (<KnowledgeGraph title={knowledgeBase?.parser_config?.graphrag?.scene_name} data={data} loading={loading} />) 
          : 
          <Empty title={t('knowledgeBase.graphEmpty')} size={88} />}
      </div>
    </Flex>
  )
}

export default KnowledgeGraphCard