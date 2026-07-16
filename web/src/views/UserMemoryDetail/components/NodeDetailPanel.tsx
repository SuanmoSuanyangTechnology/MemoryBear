/**
 * Node Detail Panel
 * Renders the detail card for the currently selected node in the relationship network.
 * Handles edge / community / regular node rendering variants.
 */
import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Flex, Divider, type SegmentedProps, Image } from 'antd'
import dayjs from 'dayjs'
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import type { Node as GraphNode, PerceptualNodeProperties, StatementNodeProperties, ExtractedEntityNodeProperties, AssistantPrunedNodeProperties } from '../types'
import type { RawCommunityNode } from '@/components/D3Graph/types'
import Tag from '@/components/Tag'
import { type Node, type EdgeClickData } from '@/components/Charts/graphNetworkUtils'
import AudioPlayer from './AudioPlayer'
import VideoPlayer from './VideoPlayer'
import EdgeDetailPanel from './EdgeDetailPanel'

const KEYS: Record<string, string[]> = {
  image: ['summary', 'keywords', 'topic', 'domain', 'scene'],
  video: ['summary', 'keywords', 'topic', 'domain', 'scene'],
  audio: ['summary', 'keywords', 'topic', 'domain', 'speaker_count'],
  last_text: ['summary', 'keywords', 'topic', 'domain', 'section_count'],
}

const getFileType = (fileType: string) => {
  return fileType.includes('image')
    ? 'image'
    : fileType.includes('video')
    ? 'video'
    : fileType.includes('audio')
    ? 'audio'
    : 'last_text'
}

interface NodeDetailPanelProps {
  selectedNode: GraphNode | RawCommunityNode | EdgeClickData
  nodes: Node[]
  activeTab: SegmentedProps['value']
  activeRelationIndex: number
  fileSize: string
  onActiveIndexChange: (index: number) => void
  onRelationChange: (index: number, direction: 'a_to_b' | 'b_to_a') => void
  onClose: () => void
  onDownload: () => void
  onForget: () => void
  onViewAll: () => void
}

const NodeDetailPanel: FC<NodeDetailPanelProps> = ({
  selectedNode,
  nodes,
  activeTab,
  activeRelationIndex,
  fileSize,
  onActiveIndexChange,
  onRelationChange,
  onClose,
  onDownload,
  onForget,
  onViewAll,
}) => {
  const { t } = useTranslation()

  return (
    <RbCard
      title={t('userMemory.memoryDetails')}
      className="rb:absolute! rb:top-4 rb:right-0 rb:w-100! rb:bg-white! rb:max-h-[calc(100vh-140px)]!"
      headerType="borderless"
      headerClassName="rb:min-h-[60px]!"
      bodyClassName={clsx('rb:px-5! rb:pt-0! rb:pb-3! rb:max-h-[calc(100vh-194px)]! rb:overflow-auto!', {
        'rb:pb-[76px]!': activeTab !== 'communityNetwork' && !(selectedNode && 'type' in selectedNode && (selectedNode as EdgeClickData).type === 'edge'),
      })}
      extra={<div className="rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/close.svg')]" onClick={onClose}></div>}
    >
      {selectedNode && 'type' in selectedNode && (selectedNode as EdgeClickData).type === 'edge'
        ? <EdgeDetailPanel
            selectedNode={selectedNode as any}
            nodes={nodes}
            t={t}
            activeRelationIndex={activeRelationIndex}
            onActiveIndexChange={onActiveIndexChange}
            onRelationChange={onRelationChange}
          />
        : <>
          <div className={clsx("rb:max-h-[calc(100vh-272px)] rb:overflow-auto", {
            'rb:max-h-[calc(100vh-269px)]': activeTab !== 'communityNetwork',
            'rb:max-h-[calc(100vh-205px)]': activeTab == 'communityNetwork',
          })}>
            {(selectedNode as RawCommunityNode).properties.community_id
              ? <div>
                  <div className="rb:font-medium rb:text-[#212332] rb:text-[16px] rb:leading-5.5 rb:pl-1">
                    {(selectedNode as RawCommunityNode).properties.name || selectedNode.id}
                  </div>
                  {(selectedNode as RawCommunityNode).properties.summary && <>
                    <div className="rb:mt-3 rb:font-medium rb:leading-5 rb:pl-1">{t('userMemory.summary')}</div>
                    <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:px-3 rb:py-2.5 rb:mt-2">
                      {(selectedNode as RawCommunityNode).properties.summary}
                    </div>
                  </>}
                  <Flex align="center" justify="space-between" className="rb:mt-5!">
                    <span className="rb:text-[#5B6167] rb:font-regular rb:pl-1">{t('userMemory.member_count')}</span>
                    <span className="rb:font-medium">{(selectedNode as RawCommunityNode).properties.member_count}{t('userMemory.member_count_desc')}</span>
                  </Flex>

                  {(selectedNode as RawCommunityNode).properties.core_entities && <>
                    <Divider className='rb:my-2.5!' />
                    <div className="rb:font-medium rb:leading-5 rb:pl-1">{t('userMemory.core_entities')}</div>
                    <ul className="rb:list-disc rb:pl-4 rb:text-[#5B6167] rb:mt-2">
                      {(selectedNode as RawCommunityNode).properties.core_entities?.map((entity, index) => <li key={index}>{entity}</li>)}
                    </ul>
                  </>}
                </div>
              : <>
                {((selectedNode as Node).name || (selectedNode as GraphNode).label === 'Conversation') &&
                  <div className="rb:font-medium rb:text-[16px] rb:text-[#212332] rb:leading-5.5 rb:mb-3">
                    {(selectedNode as Node).name || (selectedNode as GraphNode).label}
                  </div>
                }
                <Flex vertical gap={24}>
                  {(selectedNode as GraphNode).label !== 'ExtractedEntity' &&
                    <div>
                      <div className="rb:font-medium rb:leading-5">{t('userMemory.memoryContent')}</div>
                      <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                        {['Chunk', 'Dialogue', 'MemorySummary', 'AssistantOriginal'].includes((selectedNode as GraphNode).label) && 'content' in ((selectedNode as GraphNode).properties as { content: string })
                          ? ((selectedNode as GraphNode).properties as { content: string }).content
                            : (selectedNode as GraphNode).label === 'Statement' && 'statement' in ((selectedNode as GraphNode).properties as { statement: string })
                              ? ((selectedNode as GraphNode).properties as { statement: string }).statement
                              : (selectedNode as GraphNode).label === 'Perceptual' && 'summary' in ((selectedNode as GraphNode).properties as { summary: string })
                                ? ((selectedNode as GraphNode).properties as { summary: string }).summary
                                : ['AssistantOriginal', 'AssistantPruned'].includes((selectedNode as GraphNode).label ) && 'text' in ((selectedNode as GraphNode).properties as { text: string })
                                  ? ((selectedNode as GraphNode).properties as { text: string }).text
                                  : ''
                        }
                      </div>
                    </div>
                  }
                  {(selectedNode as GraphNode).label === 'ExtractedEntity' && <>
                    <div>
                      <div className="rb:font-medium rb:leading-5">{t('userMemory.ExtractedEntity_description_summary')}</div>
                      {((selectedNode as GraphNode).properties as ExtractedEntityNodeProperties).description_summary
                      ? <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:px-3 rb:py-2.5 rb:mt-2">
                        {((selectedNode as GraphNode).properties as ExtractedEntityNodeProperties).description_summary}
                        </div>
                        : <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:px-3 rb:py-4 rb:mt-2 rb:text-center rb:text-[#5B6167]">{t('userMemory.noDescriptionSummary')}</div>
                      }
                    </div>
                    <div>
                      <div className="rb:font-medium rb:leading-5 rb:mb-2">{t('userMemory.memoryDetail')}</div>
                      {(() => {
                        const description = ((selectedNode as GraphNode).properties as ExtractedEntityNodeProperties).description || [];
                        if (description.length === 0) {
                          return <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:px-3 rb:py-4 rb:mt-2 rb:text-center rb:text-[#5B6167]">{t('userMemory.noDescription')}</div>;
                        }
                        return (
                          <Flex vertical gap={8} className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                            {description.map((item, index) => {
                              const match = item.match(/^\[([^\]]+)\]\s*(.*)$/);
                              if (match) {
                                const [, timestamp, content] = match;
                                return (
                                  <div key={index} className={index === 0 ? '' : 'rb-border-t rb:pt-2'}>
                                    <div>{timestamp}</div>
                                    <div className="rb:ml-1">{content}</div>
                                  </div>
                                );
                              }
                              return <div key={index}>{item}</div>;
                            })}
                          </Flex>
                        )
                      })()}
                    </div>
                  </>}
                  <div>
                    <div className="rb:font-medium rb:leading-5">{t('userMemory.created_at')}</div>
                    <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                      {dayjs((selectedNode as Node).properties.created_at).format('YYYY-MM-DD HH:mm:ss')}
                    </div>
                  </div>

                  {(selectedNode as Node).properties.associative_memory > 0 && <div>
                    <div className="rb:font-medium rb:leading-5">{t('userMemory.associative_memory')}</div>
                    <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-1 rb:pb-4">
                      <span className="rb:text-[#155EEF] rb:font-medium">{(selectedNode as Node).properties.associative_memory}</span> {t('userMemory.unix')}{t('userMemory.associative_memory')}
                    </div>
                  </div>}


                  {(selectedNode as GraphNode).label === 'ExtractedEntity' && <>
                    {(['description_summary', 'entity_type', 'aliases'] as const).map(key => {
                      const p = (selectedNode as Node).properties as ExtractedEntityNodeProperties
                      if (p[key]) {
                        return (
                          <div key={key}>
                            <div className="rb:font-medium rb:leading-5">{t(`userMemory.ExtractedEntity_${key}`)}</div>
                            <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                              {Array.isArray(p[key]) && p[key].length > 0
                                ? p[key].map((v, i) => <div key={i}>- {v}</div>)
                                : p[key]
                              }
                            </div>
                          </div>
                        )
                      }
                      return null
                    })}
                  </>}
                  {(selectedNode as GraphNode).label === 'Perceptual' && <>
                    <Flex vertical gap={16} className="rb:w-full!">
                      {((selectedNode as GraphNode).properties as { file_path: string }).file_path
                        ? <>
                          {((selectedNode as GraphNode).properties as { file_type: string }).file_type.includes('image')
                            ? <Image src={((selectedNode as GraphNode).properties as { file_path: string }).file_path} alt={((selectedNode as GraphNode).properties as { file_name: string }).file_name} width="100%" className="rb:rounded-xl rb:h-45!" />
                            : ((selectedNode as GraphNode).properties as { file_type: string }).file_type.includes('video')
                            ? <VideoPlayer src={((selectedNode as GraphNode).properties as { file_path: string }).file_path} />
                            : ((selectedNode as GraphNode).properties as { file_type: string }).file_type.includes('audio')
                            ? <AudioPlayer
                              src={((selectedNode as GraphNode).properties as { file_path: string }).file_path}
                              fileName={((selectedNode as GraphNode).properties as { file_name: string }).file_name}
                              fileSize={fileSize}
                            />
                            : <Flex gap={11} align="center" justify="space-between" className="rb:bg-[#F6F6F6] rb:min-h-15.5! rb:rounded-xl rb:p-3!">
                              <Flex gap={12} align="center">
                                <div className="rb:w-7.5 rb:h-9 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/file.svg')]"></div>
                                <div>
                                  <div className="rb:leading-5 rb:font-medium rb:mb-1 rb:wrap-break-word rb:line-clamp-1">
                                    {((selectedNode as GraphNode).properties as { file_name: string }).file_name}
                                  </div>
                                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">
                                    {fileSize || '-'}
                                  </div>
                                </div>
                              </Flex>
                              <div
                                className="rb:size-6 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/userMemory/download.svg')] rb:hover:bg-[url('@/assets/images/userMemory/download_hover.svg')]"
                                onClick={onDownload}
                              ></div>
                            </Flex>
                          }
                        </>
                        : null
                      }
                      {KEYS[getFileType(((selectedNode as GraphNode).properties as PerceptualNodeProperties).file_type)]?.map(key => {
                        const value = ((selectedNode as GraphNode).properties as any)[key]
                        return (
                          <div key={key} className="rb:leading-5">
                            <div className="rb:mb-1">{t(`perceptualDetail.${key}`)}</div>

                            {typeof value === 'string'
                              ? <div className="rb:text-[#5B6167]">{value}</div>
                              : Array.isArray(value)
                              ? <Flex wrap gap={11}>
                                  {value.map((vo, index) => <div key={index} className="rb:bg-[#F6F6F6] rb:rounded-[13px] rb:py-1 rb:px-2 rb:text-[12px] rb:font-medium rb:leading-4.5">{vo}</div>)}
                                </Flex>
                              : '-'
                            }
                          </div>
                        )
                      })}
                    </Flex>
                  </>}
                  {(selectedNode as GraphNode).label === 'Statement' && (<>
                    {(['emotion_keywords'] as const).map(key => {
                      const p = (selectedNode as GraphNode).properties as StatementNodeProperties
                      if ((key === 'emotion_keywords' && p[key]?.length > 0) || typeof p[key] === 'string') {
                        return (
                          <div key={key}>
                            <div className="rb:font-medium rb:leading-5">{t(`userMemory.Statement_${key}`)}</div>
                            <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                              {key === 'emotion_keywords'
                                ? <Space>{p.emotion_keywords.map((v, i) => <Tag key={i}>{v}</Tag>)}</Space>
                                : p[key]}
                            </div>
                          </div>
                        )
                      }
                      return null
                    })}
                  </>)}

                  {(selectedNode as GraphNode).label === 'AssistantPruned' && <>
                    {(['memory_type'] as const).map(key => {
                      const p = (selectedNode as Node).properties as AssistantPrunedNodeProperties
                      if (p[key]) {
                        return (
                          <div key={key}>
                            <div className="rb:font-medium rb:leading-5">{t(`userMemory.AssistantPruned_${key}`)}</div>
                            <div className="rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:mt-2">
                              {Array.isArray(p[key]) && p[key].length > 0
                                ? p[key].map((v, i) => <div key={i}>- {v}</div>)
                                : p[key]}
                            </div>
                          </div>
                        )
                      }
                      return null
                    })}
                  </>}
                </Flex>
              </>}
          </div>

          {activeTab !== 'communityNetwork' &&
            <div className="rb:absolute rb:bottom-3 rb:left-6 rb:right-6">
              <Flex align="center" gap={12}>
                <Flex align="center" justify="center" gap={6} className="rb:flex-1 rb:border rb:border-[#E5E6EB] rb:rounded-xl rb:h-11 rb:font-medium rb:leading-5 rb:text-[#212332] rb:cursor-pointer rb:hover:border-[#171719]" onClick={onForget}>
                  {t('userMemory.forgetThisMemory')}
                </Flex>
                <Flex align="center" justify="center" className="rb:flex-1 rb:border rb:border-[#171719] rb:rounded-xl rb:h-11 rb:font-medium rb:leading-5 rb:cursor-pointer" onClick={onViewAll}>
                  {t('userMemory.completeMemory')}
                </Flex>
              </Flex>
            </div>
          }
        </>
      }
    </RbCard>
  )
}

export default NodeDetailPanel
