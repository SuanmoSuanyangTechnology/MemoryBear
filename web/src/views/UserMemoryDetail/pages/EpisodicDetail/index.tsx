import { useEffect, useState } from 'react'
import { Button, Flex, Form, Input, Select, Skeleton } from 'antd'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import clsx from 'clsx'

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import Tag from '@/components/Tag'
import PageScrollList from '@/components/PageScrollList'
import { formatDateTime } from '@/utils/format'
import type { EpisodicMemory, EpisodicMemoryDetail, EpisodicOverviewData, EpisodicOverviewQuery, EpisodicScene } from '@/views/UserMemoryDetail/pages/EpisodicDetail/types'
import { getEpisodicOverviewUrl, getEpisodicDetailUrl } from '@/api/memory';

const TYPE_COLORS: Record<string, 'processing' | 'error' | 'success' | 'warning' | 'default' | 'purple' | 'dark'> = {
  work_career: 'processing',
  learning_skills: 'purple',
  health_care: 'warning',
  finance_assets: 'processing',
  living_routine: 'success',
  relationships: 'error',
  leisure_experience: 'error',
  other: 'default',
}

/** Filterable item overview with the selected item's summary and scenes. */
const EpisodicDetail = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [form] = Form.useForm<Omit<EpisodicOverviewQuery, 'end_user_id'>>()
  const filters = Form.useWatch([], form) ?? { sort: 'scene_count_desc' }
  const [selected, setSelected] = useState<EpisodicMemory | null>(null)
  const [detail, setDetail] = useState<EpisodicMemoryDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [keyword, setKeyword] = useState<string | undefined>()
  const { category_l1, sort = 'scene_count_desc' } = filters
  const search = filters.keyword?.trim() ?? undefined
  const query = { end_user_id: id, category_l1, keyword, sort }

  useEffect(() => {
    if (!search) {
      setKeyword(undefined)
      return
    }
    const timer = setTimeout(() => setKeyword(search), 300)
    return () => clearTimeout(timer)
  }, [search])

  useEffect(() => {
    setSelected(null)
    setDetail(null)
    setLoading(Boolean(id))
  }, [id, category_l1, sort, keyword])

  const onOverviewLoad = (result: EpisodicOverviewData) => {
    if (result.page.page === 1) setSelected(result.items[0] ?? null)
  }
  const onOverviewLoadingChange = (value: boolean) => {
    setLoading(value)
  }

  const selectedId = selected?.id
  useEffect(() => {
    setDetail(null)
  }, [id, selectedId])

  const onDetailLoad = (result: EpisodicMemoryDetail) => {
    setDetail(result)
  }

  const itemTitle = (item: { topic_name: string | null; topic_scope: string | null }) => item.topic_name?.trim() || item.topic_scope?.trim() || t('episodicDetail.unnamedItem')
  const sceneTitle = (value: string | null) => value?.trim() || t('episodicDetail.unnamedScene')
  const renderType = (value?: string | null) => {
    const key = value || 'other'
    const known = Object.hasOwn(TYPE_COLORS, key)
    const typeKey = known ? key : 'other'
    return (
      <Tag color={TYPE_COLORS[typeKey]}>
        {known || !value ? t(`episodicDetail.${typeKey}`) : value}
      </Tag>
    )
  }

  return (
    <Flex className="rb:h-full rb:min-h-0" gap={12}>
      <RbCard
        title={t('episodicDetail.overview')}
        className="rb:w-90 rb:shrink-0 rb:overflow-hidden rb:rounded-xl! rb:h-full!"
        bodyClassName="rb:h-full rb:p-0! rb:h-[calc(100%-38px)]! rb:overflow-hidden!"
        headerClassName="rb:font-[MiSans-Bold] rb:font-bold rb:min-h-[38px]!"
        headerType="borderless"
      >
        <Flex vertical className="rb:h-full rb:min-h-0 rb:overflow-hidden!">
          <Form form={form} initialValues={{ category_l1: '', sort: 'scene_count_desc' }} className="rb:border-b rb:border-gray-200 rb:px-4! rb:pb-3! rb:mb-3!">
            <div className="rb:mb-2 rb:grid rb:grid-cols-2 rb:gap-2">
              <Form.Item name="category_l1" noStyle>
                <Select
                  aria-label={t('episodicDetail.itemType')}
                  className="rb:w-full rb:min-w-0"
                  options={[
                    { value: '', label: t('episodicDetail.allType') },
                    ...Object.keys(TYPE_COLORS).map(value => ({ value, label: t(`episodicDetail.${value}`) })),
                  ]}
                />
              </Form.Item>
              <Form.Item name="sort" noStyle>
                <Select
                  className="rb:w-full rb:min-w-0"
                  options={[
                    { value: 'scene_count_desc', label: t('episodicDetail.episodeCountFirst') },
                    { value: 'created_at_desc', label: t('episodicDetail.createdFirst') },
                    { value: 'updated_at_desc', label: t('episodicDetail.updatedFirst') },
                  ]}
                />
              </Form.Item>
            </div>
            <Form.Item name="keyword" noStyle>
              <Input maxLength={200} allowClear placeholder={t('episodicDetail.titleKeywordPlaceholder')} />
            </Form.Item>
          </Form>
          {id &&
            <PageScrollList<EpisodicMemory, typeof query>
              url={getEpisodicOverviewUrl}
              method="post"
              query={{...query, category_l1: query.category_l1 || undefined}}
              onLoad={onOverviewLoad}
              onLoadingChange={onOverviewLoadingChange}
              heightClass="rb:h-full"
              className="rb:flex-1 rb:p-3 rb:pt-0!"
              pageLoading={<Skeleton active />}
              empty={(
                <div className="rb:pt-8 rb:text-center">
                  <Empty size={88} subTitle={t('episodicDetail.noMatchingItems')} />
                  <Button onClick={() => form.resetFields()}>{t('episodicDetail.resetFilters')}</Button>
                </div>
              )}
              renderItems={items => (
                <Flex vertical gap={10}>
                  {items.map((item, index) => (
                    <div
                      key={item.id}
                      aria-pressed={selected?.id === item.id}
                      onClick={() => setSelected(item)}
                      className={clsx('rb:w-full rb:cursor-pointer rb:rounded-xl rb:border rb:bg-white rb:p-3 rb:text-left', selected?.id === item.id ? 'rb:border-primary-600' : 'rb:border-gray-200')}
                    >
                      <Flex component="span" align="start" gap={8}>
                        <Flex component="span" align="center" justify="center" className="rb:size-5.5 rb:shrink-0 rb:rounded-md rb:bg-primary-600 rb:text-xs rb:text-white">{index + 1}</Flex>
                        <span className="rb:min-w-0 rb:line-clamp-2 rb:wrap-break-word rb:text-sm rb:font-medium rb:leading-6 rb:text-gray-900">{itemTitle(item)}</span>
                      </Flex>
                      <Flex component="span" align="center" gap={8} className="rb:mt-2!">
                        {renderType(item.category_l1)}
                        <span className="rb:text-xs rb:text-gray-500">{item.scene_count == null ? '—' : t('episodicDetail.sceneCount', { count: item.scene_count })}</span>
                      </Flex>
                    </div>
                  ))}
                </Flex>
              )}
            />
          }
        </Flex>
      </RbCard>
      <RbCard
        height="100%"
        className="rb:min-w-0 rb:flex-1 rb:overflow-hidden rb:rounded-xl!"
        bodyClassName="rb:h-full rb:overflow-hidden rb:p-5!"
      >
        {loading && !selected
          ? <Skeleton active paragraph={{ rows: 3 }} />
          : !selected || !id
          ? (
            <Flex vertical align="center" justify="center" className="rb:min-h-full">
              <Empty size={88} subTitle={t('episodicDetail.noScenes')} />
              <p className="rb:m-0 rb:text-xs rb:text-gray-500">{t('episodicDetail.adjustFiltersHint')}</p>
            </Flex>
          )
          : (
            <PageScrollList<EpisodicScene>
              key={`${id}-${selected.id}`}
              url={getEpisodicDetailUrl}
              method="post"
              query={{ end_user_id: id, scene_community_id: selected.id }}
              heightClass="rb:h-full"
              pageLoading={<Skeleton active paragraph={{ rows: 3 }} />}
              onLoad={onDetailLoad}
              empty={<Empty size={88} />}
              header={detail && <>
              <div className="rb:rounded-xl rb:border rb:border-gray-200 rb:p-5">
                <h3 className="rb:mb-5 rb:wrap-break-word rb:text-base rb:font-semibold rb:text-gray-800">{itemTitle(detail)}</h3>
                <Flex wrap gap="12px 32px" className="rb:mb-6! rb:text-xs">
                  <div>
                    <div className="rb:mb-2 rb:text-gray-500">{t('episodicDetail.itemType')}</div>
                    {renderType(detail.category_l1)}
                  </div>
                  <div>
                    <div className="rb:mb-2 rb:text-gray-500">{t('episodicDetail.createdAt')}</div>
                    {detail.created_at == null ? '—' : formatDateTime(detail.created_at, 'YYYY-MM-DD HH:mm')}
                  </div>
                  <div>
                    <div className="rb:mb-2 rb:text-gray-500">{t('episodicDetail.updatedAt')}</div>
                    {detail.updated_at == null ? '—' : formatDateTime(detail.updated_at, 'YYYY-MM-DD HH:mm')}
                  </div>
                </Flex>
                <h4 className="rb:m-0 rb:mb-3 rb:border-l-3 rb:border-primary-600 rb:pl-2 rb:text-sm rb:font-medium">{t('episodicDetail.summary')}</h4>
                <div className="rb:whitespace-pre-wrap rb:wrap-break-word rb:text-sm rb:leading-6 rb:text-gray-600">{detail.summary.trim() ? detail.summary : t('episodicDetail.noSummary')}</div>
              </div>
              <Flex align="center" gap={8} className="rb:my-5!">
                <h4 className="rb:m-0 rb:border-l-3 rb:border-primary-600 rb:pl-2 rb:text-sm rb:font-medium">{t('episodicDetail.scenes')}</h4>
                <span className="rb:text-xs rb:text-gray-500">{t('episodicDetail.count', { count: detail.scene_count })}</span>
              </Flex>
              </>}
              renderItems={scenes => (
              <Flex vertical gap={12}>
                {scenes.map((scene, index) => (
                    <article id={`episodic-scene-${scene.id}`} key={scene.id} className="rb:rounded-xl rb:border rb:p-4 rb:border-gray-200">
                      <Flex align="start" gap={8} className="rb:mb-3!">
                        <Flex component="span" align="center" justify="center" className="rb:size-5.5 rb:shrink-0 rb:rounded-md rb:bg-slate-100 rb:text-xs rb:text-gray-600">{index + 1}</Flex>
                        <h4 className="rb:m-0 rb:wrap-break-word rb:text-sm rb:font-medium rb:leading-6 rb:text-gray-800">{sceneTitle(scene.topic_scope)}</h4>
                      </Flex>
                      <div className="rb:whitespace-pre-wrap rb:wrap-break-word rb:text-sm rb:leading-6 rb:text-gray-600">{scene.content.trim() ? scene.content : t('episodicDetail.noSceneContent')}</div>
                    </article>
                  ))}
              </Flex>
              )}
            />
          )
        }
      </RbCard>
    </Flex>
  )
}

export default EpisodicDetail
