import React, { useState, type FC, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Spin, Flex } from 'antd';

import type { CommunityD3Node, CommunityGraphData, RawCommunityGraphData, RawCommunityNode } from '@/components/D3Graph/types'
import { buildCommunityGraphData } from '@/components/D3Graph/utils'
import CommunityGraph from '@/components/D3Graph/CommunityGraph'
import { getMemoryCommunityGraph } from '@/api/memory'

// ─── Tooltip ──────────────────────────────────────────────────────────────────

const NodeTooltip: FC<{ node: CommunityD3Node }> = ({ node }) => {
  const { t } = useTranslation()
  return (
    <div style={{
      background: '#fff', border: '1px solid #DFE4ED', borderRadius: 8,
      boxShadow: '0 4px 16px rgba(0,0,0,0.12)', padding: '10px 14px',
      minWidth: 180, maxWidth: 260, fontSize: 13,
    }}>
      <div style={{ fontWeight: 600, marginBottom: 6, color: '#1a1a1a', fontSize: 14 }}>
        {node.properties?.name ?? node.name}
      </div>
      {node.properties?.description && (
        <div style={{ color: '#5B6167', lineHeight: '20px', marginBottom: 4 }}>
          {node.properties.description}
        </div>
      )}
      <div style={{ color: '#5B6167', lineHeight: '22px' }}>
        {t('userMemory.type')}：
        <span style={{ color: '#1a1a1a' }}>{t(`userMemory.${node.properties?.entity_type}`)}</span>
      </div>
      <div style={{ color: '#5B6167', lineHeight: '22px' }}>
        {t('userMemory.community')}：
        <span style={{ color: node.color, fontWeight: 500 }}>{node.properties?.community_name}</span>
      </div>
    </div>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

const CommunityNetwork: FC<{ onSelectCommunity?: (node: RawCommunityNode) => void }> = ({ onSelectCommunity }) => {
  const { id } = useParams()
  const { t } = useTranslation()
  const [graphData, setGraphData] = useState<CommunityGraphData | null>(null)
  const [empty, setEmpty] = useState(false)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    setEmpty(false)
    setGraphData(null)
    setLoading(true)
    getMemoryCommunityGraph(id, { signal: controller.signal }).then(res => {
      const raw = res as RawCommunityGraphData
      if (!raw.nodes?.length) { setEmpty(true); return }
      const built = buildCommunityGraphData(raw)
      if (!built) { setEmpty(true); return }
      setGraphData(built)
    }).catch((e) => { if (e?.code !== 'ERR_CANCELED') setEmpty(true) })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [id])

  if (loading) {
    return <Flex align="center" justify="center" className="rb:w-full rb:h-full">
      <Spin tip={t('userMemory.communityLoadingTip')} size="large" className="rb:text-[#5B6167]! spin">
        <div className="rb:w-64 rb:h-64" />
      </Spin>
      </Flex>
  }

  return (
    <CommunityGraph
      data={graphData}
      empty={empty}
      showLegend={false}
      onCommunityClick={onSelectCommunity}
      renderTooltip={node => <NodeTooltip node={node} />}
    />
  )
}

export default React.memo(CommunityNetwork)
