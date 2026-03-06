/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:31:50 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-04 16:22:03
 */
import { useEffect, useState, useRef, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { App } from 'antd'

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import { getEmotionSuggestions } from '@/api/memory'
import RbAlert from '@/components/RbAlert'


/**
 * Suggestions data structure
 * @property {string} health_summary - Overall health summary
 * @property {Array} suggestions - List of suggestions with actionable steps
 */
interface Suggestions {
  exists?: boolean;
  health_summary: string;
  suggestions: Array<{
    type: string;
    title: string;
    content: string;
    priority: string;
    actionable_steps: string[];
  }>;
}

/**
 * Suggestions Component
 * Displays emotional health suggestions with actionable steps
 * Shows health summary and prioritized recommendations
 */
const Suggestions = forwardRef<{ handleRefresh: () => void; }, { refresh: () => void; }>(({ refresh }, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const { modal } = App.useApp()
  const [loading, setLoading] = useState(false)
  const [suggestions, setSuggestions] = useState<Suggestions | null>(null)
  const modalInstanceRef = useRef<{ destroy: () => void } | null>(null)

  useEffect(() => {
    getSuggestionData()
    return () => modalInstanceRef.current?.destroy()
  }, [id])

  const getSuggestionData = () => {
    if (!id) {
      return
    }
    setLoading(true)
    getEmotionSuggestions(id)
      .then((res) => {
        const response = res as Suggestions
        if (!response.exists && (!response.suggestions || !response.suggestions?.length)) {
          modalInstanceRef.current = modal.warning({
            title: t('statementDetail.noData'),
            okText: t('common.refresh'),
            onOk: () => {
              refresh()
            }
          })
        } else {
          setSuggestions(res as Suggestions)
        }
      })
      .finally(() => {
        setLoading(false)
      })
  }

  useImperativeHandle(ref, () => ({
    handleRefresh: getSuggestionData
  }));
  return (
    <RbCard
      title={t('statementDetail.suggestions')}
      headerType="borderless"
      headerClassName="rb:leading-[24px] rb:bg-[#F6F8FC]! rb:min-h-[46px]! rb:border-b! rb:border-b-[#DFE4ED]!"
      bodyClassName="rb:px-[16px]! rb:pt-[20px]! rb:pb-[24px]!"
    >
      {suggestions?.suggestions && suggestions?.suggestions.length > 0
        ? <>
          <RbAlert className="rb:mb-3">{suggestions.health_summary}</RbAlert>
          <div className="rb:space-y-8">
            {suggestions.suggestions.map((item, index) => (
              <div key={index}>
                <div className="rb:font-medium">{index + 1}. {item.title}</div>
                <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-2 rb:mb-2 rb:leading-5">{item.content}</div>

                <ul className="rb:list-disc rb:ml-4 rb:text-[12px] rb:text-[#5B6167] rb:leading-5">
                  {item.actionable_steps.map((vo, idx) => <li key={idx}>{vo}</li>)}
                </ul>
              </div>
            ))}
          </div>
        </>
        : <Empty size={88} subTitle={t(loading ? 'statementDetail.suggestionLoading' : 'empty.tableEmpty')} className="rb:h-full" />
      }
    </RbCard>
  )
})

export default Suggestions