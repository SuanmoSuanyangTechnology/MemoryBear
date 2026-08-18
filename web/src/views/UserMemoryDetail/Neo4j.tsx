/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:57:26 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-18 14:38:58
 */
/**
 * Neo4j User Memory Detail View
 * Displays user memory details using Neo4j graph storage
 * Shows profile, interests, node statistics, relationships, and insights
 */

import { type FC, useRef, useState, type MouseEvent, useEffect, Suspense } from 'react'
import clsx from 'clsx'
import { useParams, useNavigate } from 'react-router-dom'
import { Flex, Popover } from 'antd'
import { useTranslation } from 'react-i18next';

import EndUserProfile from './components/EndUserProfile'
import AboutMe from './components/AboutMe'
import InterestDistribution from './components/InterestDistribution'
import NodeStatistics, { type NodeStatisticsRef } from './components/NodeStatistics'
import RelationshipNetwork, { type RelationshipNetworkRef } from './components/RelationshipNetwork'
import MemoryInsight from './components/MemoryInsight'
import type { EndUserProfileRef, MemoryInsightRef, AboutMeRef, EndUser } from './types'
import {
  analyticsRefresh,
} from '@/api/memory'
import { useI18n } from '@/store/locale'
import PrivateWrap from '@/components/PrivateWrap'
import { BrainView,
  ReflectMemory, ReflectMemoryPanel, type ReflectMemoryRef,
  MemoryValueRank, MemoryValueRankPanel, type MemoryValueRankPanelRef
} from '@redbear/memory-brick'
import { request } from '@/utils/request'
import MemoryActivity from './components/MemoryActivity'
import { SIDEBAR_MENU_ITEMS } from './constant'

const Neo4j: FC = () => {
  const { id } = useParams()
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false)
  const [name, setName] = useState('')
  const { language } = useI18n()
  const ref = useRef<EndUserProfileRef>(null)
  const memoryInsightRef = useRef<MemoryInsightRef>(null)
  const aboutMeRef = useRef<AboutMeRef>(null)
  const brainViewRef = useRef(null)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [brainMemories, setBrainMemories] = useState<string[]>([])
  const [regionId, setRegionId] = useState<string | null>(null)
  const nodeStatisticsRef = useRef<NodeStatisticsRef>(null)
  const memoryValueRankPanelRef = useRef<MemoryValueRankPanelRef>(null)
  const relationshipNetworkRef = useRef<RelationshipNetworkRef>(null)
  const reflectMemoryRef = useRef<ReflectMemoryRef>(null)

  /** Handle brain region memory types change */
  const handleBrainMemoriesChange = (memories: string[], regionId: string | null) => {
    setBrainMemories(memories)
    setRegionId(regionId)
  }

  /** Update displayed name */
  const handleNameUpdate = (data?: EndUser) => {
    if (!data) return
    let name = data.other_name && data.other_name !== '' ? data.other_name : data.id || data.end_user_id
    setName(name)
  }
  useEffect(() => {
    document.title = `${name} - ${t('memoryBear')}`;
  }, [name, language])

  /** Navigate back */
  const goBack = () => {
    navigate('/user-memory', { replace: true })
  }

  /** Refresh analytics data */
  const handleRefresh = () => {
    if (loading) return;
    setLoading(true)
    analyticsRefresh(id as string)
      .then(res => {
        const response = res as { insight_success: boolean; summary_success: boolean; }
        if (response.insight_success) {
          memoryInsightRef.current?.getData()
        }
        if (response.summary_success) {
          aboutMeRef.current?.getData()
        }
      })
      .finally(() => {
        setLoading(false)
      })
  }
  const [selectNodeId, setSelectNodeId] = useState<string | null>(null)
  const onOpenChange = (e: MouseEvent, type: string) => {
    e.preventDefault();
    e.stopPropagation();
    setSelectedKey(type)
    setSelectNodeId(null)
    if (type !== 'Brain') {
      setBrainMemories([]);
      setRegionId(null);
    }
    relationshipNetworkRef.current?.reset()
  }

  return (
    <Flex gap={12} className="rb:h-full! rb:w-screen rb:p-3! rb:relative!" onClick={() => { setSelectedKey(null); setBrainMemories([]) }}>
      <Flex gap={16} vertical justify="space-between" align="center"
        className="rb:h-full! rb:px-4! rb:pt-6! rb:pb-5! rb:bg-white rb:w-20 rb:rounded-xl"
      >
        <Popover
          content={t('userMemory.memoryWindow', { name: name })}
          placement="right"
          arrow={false}
          trigger="hover"
        >
          <div className="rb:size-12 rb:rounded-xl rb:bg-cover rb:bg-[url('@/assets/images/userMemory/logo.png')]"></div>
        </Popover>
        <Flex gap={16} vertical className="rb:flex-1! rb:mt-4! rb:overflow-y-auto">
          <PrivateWrap>
            <Flex
              align="center"
              justify="center"
              className={clsx("rb:cursor-pointer rb:size-12 rb:rounded-xl rb:group rb:shrink-0", {
                'rb:bg-[#171719]': selectedKey === 'Brain',
                'rb:hover:bg-[#EBEBEB]': selectedKey !== 'Brain',
              })}
              onClick={(e) => onOpenChange(e, 'Brain')}
            >
              <div className={clsx("rb:size-6 rb:bg-cover", {
                "rb:bg-[url('@/assets/images/userMemory/brain.svg')]": selectedKey !== 'Brain',
                "rb:bg-[url('@/assets/images/userMemory/brain_active.svg')]": selectedKey === 'Brain'
              })}></div>
            </Flex>
          </PrivateWrap>

          {SIDEBAR_MENU_ITEMS.map((item) => {
            const isSelected = selectedKey === item.key

            return (
              <Flex
                key={item.key}
                align="center"
                justify="center"
                className={clsx("rb:cursor-pointer rb:size-12 rb:rounded-xl rb:group rb:shrink-0", {
                  'rb:bg-[#171719]': isSelected,
                  'rb:hover:bg-[#EBEBEB]': !isSelected,
                })}
                onClick={(e) => onOpenChange(e, item.key)}
              >
                <div
                  className={clsx(
                    'rb:size-6 rb:bg-cover rb:shrink-0',
                    isSelected ? item.activeIconClassName : item.iconClassName,
                  )}
                ></div>
              </Flex>
            )
          })}

          <PrivateWrap>
            {() => (
              <ReflectMemory
                onOpenChange={(e: MouseEvent) => {
                  onOpenChange(e, 'reflect')
                }}
                selectedKey={selectedKey}
              />
            )}
          </PrivateWrap>

          <PrivateWrap>
            {() => (
              <MemoryValueRank
                onOpenChange={(e: MouseEvent) => {
                  onOpenChange(e, 'rank')
                }}
                selectedKey={selectedKey}
              />
            )}
          </PrivateWrap>
        </Flex>

        <Flex vertical gap={24} className="rb:shrink-0!">
          <div className={clsx("rb:shrink-0 rb:cursor-pointer rb:size-6 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/refresh.svg')]", {
            "rb:animate-spin": loading
          })} onClick={handleRefresh}></div>
          <div className="rb:shrink-0 rb:cursor-pointer rb:size-6 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/logout.svg')]" onClick={goBack}></div>
        </Flex>
      </Flex>

      <Flex vertical className="rb:flex-1 rb:min-w-0">
        <NodeStatistics ref={nodeStatisticsRef} highlightKeys={brainMemories} />
        <div className="rb:flex-1 rb:relative">
          <div onClick={(e) => e.stopPropagation()}
            className={clsx("rb:absolute rb:w-full rb:h-full", {
              'rb:hidden': !selectedKey,
              'rb:block': selectedKey,
            })}
          >
            <EndUserProfile ref={ref} onDataLoaded={handleNameUpdate} className={selectedKey === 'userProfile' ? 'rb:block!' : 'rb:hidden!'} />
            <AboutMe ref={aboutMeRef} className={selectedKey === 'aboutMe' ? 'rb:block!' : 'rb:hidden!'} />
            <Suspense fallback={null}>
              <PrivateWrap>
                {() => (
                  <BrainView
                    ref={brainViewRef}
                    visible={selectedKey === 'Brain'}
                    className={selectedKey === 'Brain' ? 'rb:block!' : 'rb:hidden!'}
                    onMemoriesChange={handleBrainMemoriesChange}
                    onClose={() => { setSelectedKey((prev) => (prev === 'Brain' ? null : prev)); setBrainMemories([]); setRegionId(null) }}
                  />
                )}
              </PrivateWrap>
            </Suspense>
            <InterestDistribution className={selectedKey === 'interestDistribution' ? 'rb:block!' : 'rb:hidden!'} />
            <MemoryInsight ref={memoryInsightRef} className={selectedKey === 'memoryInsight' ? 'rb:block!' : 'rb:hidden!'} />

            <PrivateWrap>
              {() => (
                <ReflectMemoryPanel
                  ref={reflectMemoryRef}
                  request={request}
                  selectedKey={selectedKey}
                />
              )}
            </PrivateWrap>
            <PrivateWrap>
              {() => (
                <MemoryValueRankPanel
                  ref={memoryValueRankPanelRef}
                  request={request}
                  selectedKey={selectedKey}
                  onSelectNode={(item: any) => {
                    console.log('onSelectNode item', item)
                    setSelectNodeId(item.id)
                  }}
                />
              )}
            </PrivateWrap>
          </div>
          <RelationshipNetwork
            ref={relationshipNetworkRef}
            regionId={regionId}
            setRegionId={setRegionId}
            selectedKey={selectedKey}
            setSelectedKey={setSelectedKey}
            setBrainMemories={setBrainMemories}
            refresh={() => {
              nodeStatisticsRef.current?.getData()
              reflectMemoryRef.current?.getData()
            }}
            selectNodeId={selectNodeId}
          />
        </div>
      </Flex>
      {id && <MemoryActivity id={id} />}
    </Flex>
  )
}
export default Neo4j