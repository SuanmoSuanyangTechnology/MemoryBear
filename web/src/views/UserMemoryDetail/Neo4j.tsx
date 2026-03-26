/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:57:26 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-09 14:28:34
 */
/**
 * Neo4j User Memory Detail View
 * Displays user memory details using Neo4j graph storage
 * Shows profile, interests, node statistics, relationships, and insights
 */

import { type FC, useRef, useState, type MouseEvent } from 'react'
import clsx from 'clsx'
import { useParams, useNavigate } from 'react-router-dom'
import { Flex, Popover } from 'antd'
import { useTranslation } from 'react-i18next';

import EndUserProfile from './components/EndUserProfile'
import AboutMe from './components/AboutMe'
import InterestDistribution from './components/InterestDistribution'
import NodeStatistics from './components/NodeStatistics'
import RelationshipNetwork from './components/RelationshipNetwork'
import MemoryInsight from './components/MemoryInsight'
import type { EndUserProfileRef, MemoryInsightRef, AboutMeRef } from './types'
import {
  analyticsRefresh,
} from '@/api/memory'

const Neo4j: FC = () => {
  const { id } = useParams()
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false)
  const [name, setName] = useState('')
  const ref = useRef<EndUserProfileRef>(null)
  const memoryInsightRef = useRef<MemoryInsightRef>(null)
  const aboutMeRef = useRef<AboutMeRef>(null)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)

  /** Update displayed name */
  const handleNameUpdate = (data: { other_name?: string; id: string }) => {
    setName(data.other_name && data.other_name !== '' ? data.other_name : data.id)
  }

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

  const onOpenChange = (e: MouseEvent, type: string) => {
    e.preventDefault();
    e.stopPropagation();
    setSelectedKey(type)
  }

  return (
    <div className="rb:h-screen rb:w-screen rb:p-3 rb:relative" onClick={() => setSelectedKey(null)}>
      <Flex className="rb:h-[calc(100vh-24px)]" gap={12}>
        <Flex gap={15} vertical justify="space-between" align="center" className="rb:h-full! rb:px-4! rb:pt-6! rb:pb-5! rb:bg-white rb:w-20 rb:rounded-xl">
          <Flex gap={15} vertical>
            <Popover 
              content={t('userMemory.memoryWindow', { name: name })}
              placement="right"
              arrow={false}
              trigger="hover"
            >
              <div className="rb:mb-4.25! rb:size-12 rb:rounded-xl rb:bg-cover rb:bg-[url('@/assets/images/userMemory/logo.png')]"></div>
            </Popover>

            <Flex 
              align="center"
              justify="center"
              className={clsx("rb:cursor-pointer rb:size-12 rb:rounded-xl rb:group", {
                'rb:bg-[#155EEF]': selectedKey === 'userProfile',
                'rb:hover:bg-[#F0F3F8]': selectedKey !== 'userProfile',
              })}
              onClick={(e) => onOpenChange(e, 'userProfile')}
            >
              <div className={clsx("rb:size-6 rb:bg-cover", {
                "rb:bg-[url('@/assets/images/userMemory/userProfile.svg')]": selectedKey !== 'userProfile',
                "rb:bg-[url('@/assets/images/userMemory/userProfile_active.svg')]": selectedKey === 'userProfile'
              })}></div>
            </Flex>

            <Flex
              align="center"
              justify="center"
              className={clsx("rb:cursor-pointer rb:size-12 rb:rounded-xl rb:group", {
                'rb:bg-[#155EEF]': selectedKey === 'aboutMe',
                'rb:hover:bg-[#F0F3F8]': selectedKey !== 'aboutMe',
              })}
              onClick={(e) => onOpenChange(e, 'aboutMe')}
            >
              <div className={clsx("rb:size-6 rb:bg-cover", {
                "rb:bg-[url('@/assets/images/userMemory/aboutMe.svg')]": selectedKey !== 'aboutMe',
                "rb:bg-[url('@/assets/images/userMemory/aboutMe_active.svg')]": selectedKey === 'aboutMe'
              })}></div>
            </Flex>

            <Flex
              align="center"
              justify="center"
              className={clsx("rb:cursor-pointer rb:size-12 rb:rounded-xl rb:group", {
                'rb:bg-[#155EEF]': selectedKey === 'interestDistribution',
                'rb:hover:bg-[#F0F3F8]': selectedKey !== 'interestDistribution',
              })}
              onClick={(e) => onOpenChange(e, 'interestDistribution')}
            >
              <div className={clsx("rb:size-6 rb:bg-cover", {
                "rb:bg-[url('@/assets/images/userMemory/interestDistribution.svg')]": selectedKey !== 'interestDistribution',
                "rb:bg-[url('@/assets/images/userMemory/interestDistribution_active.svg')]": selectedKey === 'interestDistribution'
              })}></div>
            </Flex>

            <Flex
              align="center"
              justify="center"
              className={clsx("rb:cursor-pointer rb:size-12 rb:rounded-xl rb:group", {
                'rb:bg-[#155EEF]': selectedKey === 'memoryInsight',
                'rb:hover:bg-[#F0F3F8]': selectedKey !== 'memoryInsight',
              })}
              onClick={(e) => onOpenChange(e, 'memoryInsight')}
            >
              <div className={clsx("rb:size-6 rb:bg-cover", {
                "rb:bg-[url('@/assets/images/userMemory/memoryInsight.svg')]": selectedKey !== 'memoryInsight',
                "rb:bg-[url('@/assets/images/userMemory/memoryInsight_active.svg')]": selectedKey === 'memoryInsight'
              })}></div>
            </Flex>
          </Flex>

          <Flex vertical gap={24}>
            <div className={clsx("rb:cursor-pointer rb:size-6 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/refresh.svg')]", {
              "rb:animate-spin": loading
            })} onClick={handleRefresh}></div>
            <div className="rb:cursor-pointer rb:size-6 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/logout.svg')]" onClick={goBack}></div>
          </Flex>
        </Flex>
        
        <Flex vertical className="rb:flex-1">
          <NodeStatistics />
          <RelationshipNetwork />
        </Flex>
      </Flex>
      <div onClick={(e) => e.stopPropagation()}>
        <EndUserProfile ref={ref} onDataLoaded={handleNameUpdate} className={selectedKey === 'userProfile' ? 'rb:block!' : 'rb:hidden!'} />
        <AboutMe ref={aboutMeRef} className={selectedKey === 'aboutMe' ? 'rb:block!' : 'rb:hidden!'} />
        <InterestDistribution className={selectedKey === 'interestDistribution' ? 'rb:block!' : 'rb:hidden!'} />
        <MemoryInsight ref={memoryInsightRef} className={selectedKey === 'memoryInsight' ? 'rb:block!' : 'rb:hidden!'} />
      </div>
    </div>
  )
}
export default Neo4j