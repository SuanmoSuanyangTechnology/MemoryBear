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
    <div className="rb:min-w-45 rb:max-w-65 rb:rounded-lg rb:border rb:border-[#DFE4ED] rb:bg-white rb:px-3.5 rb:py-2.5 rb:text-[13px] rb:shadow-[0_4px_16px_rgba(0,0,0,0.12)]">
      <div className="rb:mb-1.5 rb:text-sm rb:font-semibold rb:text-[#1a1a1a]">
        {node.properties?.name ?? node.name}
      </div>
      {node.properties?.description && (
        <div className="rb:mb-1 rb:leading-5 rb:text-[#5B6167]">
          {node.properties.description}
        </div>
      )}
      <div className="rb:leading-5.5 rb:text-[#5B6167]">
        {t('userMemory.type')}：
        <span className="rb:text-[#1a1a1a]">{t(`userMemory.${node.properties?.entity_type}`)}</span>
      </div>
      <div className="rb:leading-5.5 rb:text-[#5B6167]">
        {t('userMemory.community')}：
        <span className="rb:font-medium" style={{ color: node.color }}>{node.properties?.community_name}</span>
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
  return <Flex align="center" justify="center" className="rb:w-full rb:h-full spin">
      <Spin tip={t('userMemory.communityLoadingTip')} size="large" className="rb:text-[#5B6167]!">
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
