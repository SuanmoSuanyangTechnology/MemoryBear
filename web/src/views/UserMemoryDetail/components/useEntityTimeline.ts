import { useEffect, useState } from 'react'
import { Form } from 'antd'
import { getEntityEventTimeline } from '@/api/memory'
import {
  timelineSortOptions,
  type TimelineTab,
  type TimelineFormValues,
  type TimelineResponse,
} from './entityTimelineTypes'

interface EntityTimelineOptions {
  id?: string | null;
  end_user_id?: string | null;
  enabled: boolean;
  initialTab: TimelineTab;
  filterByType?: boolean;
  sourceKey?: string;
}

/** 统一时间线请求、筛选、分页及过期响应清理。 */
export const useEntityTimeline = ({
  id, end_user_id, enabled, initialTab, filterByType = true, sourceKey,
}: EntityTimelineOptions) => {
  const [activeTab, setActiveTab] = useState(initialTab)
  const [form] = Form.useForm<TimelineFormValues>()
  const [formValues, setFormValues] = useState<TimelineFormValues>({
    sort: timelineSortOptions[initialTab][0],
  })
  const [currentPagination, setCurrentPagination] = useState({ page: 1, pagesize: 10 })
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<TimelineResponse>({
    items: [], type_stats: [], page: { total: 0 }, total_count: 0, filtered_counts: { total: 0 }
  })

  useEffect(() => {
    let active = true
    const clear = () => setData({
      items: [], type_stats: [], page: { total: 0 }, total_count: 0, filtered_counts: { total: 0 }
    })
    if (!enabled || (!id && !end_user_id)) {
      clear()
      setLoading(false)
      return
    }
    setLoading(true)
    getEntityEventTimeline({
      ...(id ? { id: id } : {}),
      ...(end_user_id ? { end_user_id: end_user_id } : {}),
      ...(filterByType ? { type: activeTab === 'all' ? undefined : activeTab } : {}),
      ...currentPagination,
      sort: formValues.sort,
      from_ts: formValues.timeRange?.[0]?.startOf('day').valueOf(),
      to_ts: formValues.timeRange?.[1]?.endOf('day').valueOf(),
    })
      .then(res => {
        if (active) setData(res as TimelineResponse)
      })
      .catch(() => {
        if (active) clear()
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [id, enabled, sourceKey, activeTab, filterByType, currentPagination, formValues])

  const handleValuesChange = (_: unknown, values: TimelineFormValues) => {
    setFormValues(values)
    setCurrentPagination(previous => ({ ...previous, page: 1 }))
  }
  const handleChangeTab = (key: string) => {
    if (key !== 'all' && key !== 'key_node' && key !== 'statement' && key !== 'memory_summary') return
    setActiveTab(key)
    const values = { sort: timelineSortOptions[key][0], timeRange: undefined }
    form.setFieldsValue(values)
    handleValuesChange(undefined, values)
  }
  const handlePageChange = (page: number, pagesize: number) => {
    setCurrentPagination({ page, pagesize })
  }

  return {
    activeTab, form, loading, currentPagination,
    items: data.items || [],
    typeStats: data.type_stats || [],
    total: data.page.total,
    totalCount: data.filtered_counts?.total ?? 0,
    handleChangeTab, handleValuesChange, handlePageChange,
  }
}
