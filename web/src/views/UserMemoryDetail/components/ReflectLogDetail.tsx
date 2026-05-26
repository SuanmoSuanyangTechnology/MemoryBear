/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-26 15:39:10 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-26 15:46:06
 */
import { useState, useEffect, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { Skeleton, Flex } from 'antd'
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import RbDrawer from '@/components/RbDrawer'
import Tag, { type TagProps } from '@/components/Tag'
import RbAlert from '@/components/RbAlert'
import type { ReflectLog } from './ReflectMemory'
import { getReflectLogDetail } from '@/api/memory'
import Empty from '@/components/Empty'

/**
 * Entity data structure
 */
interface EntityDataTriggerDetail {
  entity_id: string;
  // entity_dedup
  name?: string;
  aliases?: string[];
  description?: string;
  entity_type?: string;

  // description_merge
  entity_name?: string;
  fragment_count?: number;
  original_description?: string;
}
interface EntityData {
    reason: string;
    entity_a: EntityDataTriggerDetail;
    entity_b: EntityDataTriggerDetail;
  }
interface DescriptionMerge {
  entity_id: string;
  entity_name?: string;
  fragment_count?: number;
  original_description?: string;
}
interface SolutionDetailChange {
  field: string;
  old: string;
  new: string;
}

/**
 * Execution step
 */
interface ExecutionStep {
  name: string;
  type: 'prompt' | 'llm' | 'decide' | 'write';
  output: string;
  success: boolean;
  duration_ms: number;
}


/**
 * Detail data structure matching the new layout
 */
interface DetailData {
  id: string;
  end_user_id: string;
  sub_problem: ReflectLog['sub_problem'];
  trigger_type: ReflectLog['trigger_type'];
  baseline: string;
  strategy: string;
  confidence: number;
  status: ReflectLog['status'];
  summary_text: string;
  created_at: number;
  entity_ids: string[];
  statement_ids: string[] | null;

  trigger_detail: EntityData | DescriptionMerge;
  solution_detail: {
    title: string;
    changes: SolutionDetailChange[];
  };
  execution_detail: {
    model: string;
    steps: ExecutionStep[];
    total_ms: number;
  };
}

export interface ReflectLogDetailRef {
  handleOpen: (record?: ReflectLog) => void
}

const bgColors: Record<ExecutionStep['type'], string> = {
  prompt: 'rb:bg-[#171719]',
  llm: 'rb:bg-[#155EEF]',
  decide: 'rb:bg-[#FF5D34]',
  write: 'rb:bg-[#369F21]',
}
const ReflectLogDetail = forwardRef<ReflectLogDetailRef>((_, ref) => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<DetailData | null>(null)
  const [currentRecord, setCurrentRecord] = useState<ReflectLog | null>(null)

  useImperativeHandle(ref, () => ({
    handleOpen
  }))

  useEffect(() => {
    getData()
  }, [currentRecord])

  const handleOpen = (record?: ReflectLog) => {
    if (record) setCurrentRecord(record)
    setOpen(true)
    getData(record)
  }

  /** Fetch reflect log detail data */
  const getData = (record?: ReflectLog) => {
    if (!record?.id) return
    setLoading(true)
    getReflectLogDetail(record?.id || '')
    .then((res) => {
      setData((res as DetailData) || null)
      setLoading(false)
    })
  }

  /** Get status tag color */
  const getStatusColor = (status: string) => {
    const colorMap: Record<string, TagProps['color']> = {
      resolved: 'success',
      pending: 'warning',
      conflict: 'error',
    }
    return colorMap[status] || 'default'
  }

  return (
    <RbDrawer
      title={<Flex align="center" gap={8}>
      {t('userMemory.reflectDetail')}
      {data && <>
        <Tag color="default">#{data.id.split('-')[0]}</Tag>
        <Tag color="processing">{t(`userMemory.${data.sub_problem}`)}</Tag>
        <Tag color={getStatusColor(data.status)}>{t(`userMemory.${data.status}`)}</Tag>
      </>}
      </Flex>}
      open={open}
      onClose={() => setOpen(false)}
    >
      <>
        {loading
          ? <Skeleton />
          : data
          ? (
            <Flex vertical gap={12}>
              {/* Trigger Detail */}
              <RbCard
                title={() => (
                  <Flex align="center" gap={8}>
                    <span className="rb:font-[MiSans-Bold] rb:font-medium">{t('userMemory.triggerReason')}</span>
                    <span className="rb:text-[12px] rb:text-[#5B6167]">trigger_detail</span>
                  </Flex>
                )}
                variant="outlined"
                headerType="borderless"
                headerClassName="rb:min-h-[46px]!"
              >
                <Flex vertical gap={12}>
                  {/* Entity A and B */}
                  {data.sub_problem === 'entity_dedup' &&
                    <div className="rb:grid rb:grid-cols-2 rb:gap-4">
                      {(['entity_a', 'entity_b'] as const).map((key) => {
                        const triggerDetail = data.trigger_detail as EntityData
                        const entity: EntityDataTriggerDetail = triggerDetail[key]
                        return (
                          <Flex vertical gap={8} key={key} className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3!">
                            <div className="rb:text-[12px] rb:text-[#5B6167]">
                              {t(`userMemory.${key}`)} {key === 'entity_a' ? ' (keeper)' : '(loser)'}
                            </div>

                            <div className="rb:text-[18px] rb:font-bold">
                              {entity.name}
                            </div>
                            {entity.entity_type &&
                              <div className="rb:text-[12px]">
                                type: {entity.entity_type}
                              </div>
                            }
                            {entity.description &&
                              <div className="rb:text-[12px] rb:text-[#5B6167]">
                                {entity.description}
                              </div>
                            }
                            <Flex wrap gap={8}>
                              {entity.aliases?.map((alias, index) => (
                                <div key={index} className="rb:bg-[#FFFFFF] rb:px-1 rb:py-0.5 rb:rounded-md rb:text-[12px] rb:text-[#5B6167] rb:mb-1">{alias}</div>
                              ))}
                            </Flex>
                          </Flex>
                        )
                      })}
                    </div>
                  }

                  {data.sub_problem === 'description_merge' && <>
                    {(() => {
                      const trigger_detail = data.trigger_detail as DescriptionMerge
                      return <>
                        <Flex vertical gap={8} className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3!">
                          <div className="rb:text-[12px] rb:text-[#5B6167]">
                            {t(`userMemory.entity`)}
                          </div>
                          <div className="rb:text-[18px] rb:font-bold mb-2">
                            {trigger_detail.entity_name}
                          </div>

                          <div className="rb:text-[12px] rb:text-[#5B6167]">
                            {t(`userMemory.entity_id`)}: {trigger_detail.entity_id}
                          </div>
                          <div className="rb:text-[12px] rb:text-[#5B6167]">
                            {t('userMemory.fragment_count')}: {trigger_detail.fragment_count}
                          </div>
                        </Flex>
                        <div className="rb:text-[12px] rb:text-[#5B6167]">
                          {t(`userMemory.original_description`)}
                        </div>
                        <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3 rb:text-[12px]">
                          {trigger_detail.original_description}
                        </div>
                      </>
                    })()}
                  </>}

                  {(data.trigger_detail as EntityData).reason &&
                    <RbAlert color="orange">
                      <div className="rb:text-[12px] rb:text-[#5B6167] mb-1">{t('userMemory.llmReason')}: &nbsp;</div>
                      <div className="rb:text-[12px] rb:text-[#212332]">{(data.trigger_detail as EntityData).reason}</div>
                    </RbAlert>
                  }

                  {/* Metadata */}
                  <Flex gap={8} className="rb:bg-[#F6F6F6] rb:rounded-lg rb:px-3! rb:py-2! rb:text-[12px] rb:text-[#5B6167]">
                    <span>
                      <span className="rb:font-medium">baseline: </span>
                      <span className="rb:text-[#5B6167]">{data.baseline}</span>
                    </span>
                    {' · '}
                    <span>
                      <span className="rb:font-medium">trigger: </span>
                      <span className="rb:text-[#5B6167]">{t(`userMemory.${data.trigger_type}`)}</span>
                    </span>
                    {data.confidence && <>
                      {' · '}
                      <span>
                        <span className="rb:font-medium">confidence: </span>
                        <span className="rb:text-[#5B6167]">{data.confidence}</span>
                      </span>
                    </>}
                  </Flex>
                </Flex>
              </RbCard>

              {/* Solution Detail */}
              <RbCard
                title={() => (
                  <Flex align="center" gap={8}>
                    <span className="rb:font-[MiSans-Bold] rb:font-medium">{t('userMemory.solution')}</span>
                    <span className="rb:text-[12px] rb:text-[#5B6167]">solution_detail</span>
                  </Flex>
                )}
                variant="outlined"
                headerType="borderless"
                headerClassName="rb:min-h-[46px]!"
              >
                <Flex vertical gap={12}>
                  <RbAlert>
                    <span className="rb:text-blue-600 rb:font-medium">
                      {data.solution_detail.title}
                    </span>
                  </RbAlert>
                  {data.solution_detail.changes.length > 0
                    ? <Flex vertical gap={8} className="rb:text-[12px] rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3!">
                      {data.solution_detail.changes.map(change => (
                        <Flex gap={12} key={change.field}>
                          <div className="rb:w-30 rb:text-[#5B6167] rb:font-medium">{change.field}</div>
                          <Flex gap={8} className="rb:flex-1!">
                            <div
                              className={clsx({
                                "rb:text-[#FF5D34] rb:line-through": !!change.old,
                              })}
                            >{change.old || '∅'}</div>
                            <div className="rb:text-[#5B6167]">→</div>
                            <div className={clsx({
                              "rb:text-[#369F21]": !!change.new,
                            })} >{change.new || '∅'}</div>
                          </Flex>
                        </Flex>
                      ))}
                    </Flex>
                    : <Empty size={88} subTitle={t('userMemory.solution_detail_noChanges')} />
                  }
                </Flex>
              </RbCard>

              {/* Execution Detail */}
              <RbCard
                title={() => (
                  <Flex align="center" gap={8}>
                    <span className="rb:font-[MiSans-Bold] rb:font-medium">{t('userMemory.execution')}</span>
                    <span className="rb:text-[12px] rb:text-[#5B6167]">execution_detail</span>
                  </Flex>
                )}
                variant="outlined"
                headerType="borderless"
                headerClassName="rb:min-h-[46px]!"
                className="rb:mb-3!"
              >
                <Flex vertical gap={12}>
                  {/* Execution Steps */}
                  <Flex vertical gap={8} className="rb:bg-[#F6F6F6] rb:rounded-lg rb:px-4! rb:py-3.5! rb:text-[12px]">
                    {data.execution_detail.steps.map((step, index) => (
                      <div key={index} className="rb:bg-[#FFFFFF] rb:rounded-xl rb:p-3">
                        <Flex align="center" justify="space-between" gap={8}>
                          <Flex align="center" gap={8}>
                            <Flex align="center" justify="center"
                              className={clsx("rb:size-6 rb:text-[#FFFFFF] rb:rounded-lg", bgColors[step.type])}
                            >
                              {step.type[0].toUpperCase()}
                            </Flex>
                            <div className="rb:font-medium">{t(`userMemory.${step.type}`)}</div>
                            <div className="rb:text-[#5B6167]">{step.type}</div>
                          </Flex>
                          <span className="rb:text-[#5B6167]">{step.duration_ms}ms</span>
                        </Flex>
                        {step.output && (
                          <div className="rb:text-[12px] rb:text-[#5B6167] rb:pl-8">{step.output}</div>
                        )}
                      </div>
                    ))}
                  </Flex>

                  {/* Total */}
                  <Flex gap={8} className="rb:bg-[#F6F6F6] rb:rounded-lg rb:px-3! rb:py-2! rb:text-[12px] rb:text-[#5B6167]">
                    <span>
                      <span className="rb:font-medium">{t('userMemory.total')}: &nbsp;</span>
                      <span className="rb:text-[#5B6167]">{data.execution_detail.total_ms}ms</span>
                    </span>
                      {' · '}
                      <span>
                        <span className="rb:font-medium">model: &nbsp;</span>
                        <span className="rb:text-[#5B6167]">{data.execution_detail.model}</span>
                      </span>
                  </Flex>
                </Flex>
              </RbCard>
            </Flex>
          )
          : null
        }
      </>
    </RbDrawer>
  )
})
export default ReflectLogDetail