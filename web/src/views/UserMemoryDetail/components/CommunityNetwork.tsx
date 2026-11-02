import React, { useState, type FC, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Spin, Flex } from 'antd';

import type { CommunityD3Node, CommunityGraphData, RawCommunityNode, RawCommunityGraphData } from '@/components/D3Graph/types'
import { buildCommunityGraphData } from '@/components/D3Graph/utils'
import CommunityGraph from '@/components/D3Graph/CommunityGraph'
import { getMemoryCommunityGraph } from '@/api/memory'
import CommunityEntityDetails from './CommunityEntityDetails'

const COMMUNITY_COLORS = ['#526A86', '#9B706A', '#6C8B75', '#8A78A4', '#A58957', '#528C91', '#A66F89', '#7182A0']

// ─── Tooltip ──────────────────────────────────────────────────────────────────

const NodeTooltip: FC<{ node: CommunityD3Node }> = ({ node }) => {
  return (
    <div className="rb:min-w-45 rb:max-w-65 rb:max-h-62.5 rb:overflow-y-auto rb:rounded-lg rb-border rb:bg-white rb:px-3.5 rb:py-2.5 rb:text-[13px] rb:shadow-[0_4px_16px_rgba(0,0,0,0.12)]">
      <CommunityEntityDetails node={node} variant="hover" />
    </div>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

const CommunityNetwork: FC<{
  onSelectCommunity?: (node: RawCommunityNode) => void
  onSelectNode?: (node: CommunityD3Node) => void
  selectedNodeId?: string | null
  selectedCommunityId?: string | null
}> = ({ onSelectCommunity, onSelectNode, selectedNodeId, selectedCommunityId }) => {
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
      if (controller.signal.aborted) return
      const built = buildCommunityGraphData(res as RawCommunityGraphData, COMMUNITY_COLORS)
      if (!built) { setEmpty(true); return }
      setGraphData(built)
    }).catch(() => { if (!controller.signal.aborted) setEmpty(true) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [id])

  if (loading) {
    return (
      <Flex align="center" justify="center" className="rb:w-full rb:h-full spin">
        <Spin tip={t('userMemory.communityLoadingTip')} size="large" className="rb:text-gray-600!">
          <div className="rb:w-64 rb:h-64" />
        </Spin>
      </Flex>
    )
  }

  return (
    <CommunityGraph
      data={graphData}
      empty={empty}
      colors={COMMUNITY_COLORS}
      showLegend={false}
      onCommunityClick={onSelectCommunity}
      onNodeClick={onSelectNode}
      selectedNodeId={selectedNodeId}
      selectedCommunityId={selectedCommunityId}
      renderTooltip={node => <NodeTooltip node={node} />}
    />
  )
}

export default React.memo(CommunityNetwork)
