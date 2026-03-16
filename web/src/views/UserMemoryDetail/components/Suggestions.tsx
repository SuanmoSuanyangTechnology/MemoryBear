/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:31:50 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-16 15:02:00 
 */
import { useEffect, useState, useRef, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { App, Flex } from 'antd'

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
      headerClassName="rb:min-h-[46px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:p-3! rb:pt-0! rb:h-[740px]"
    >
      {suggestions?.suggestions && suggestions?.suggestions.length > 0
        ? <Flex vertical gap={16} className="rb:h-full! rb:overflow-y-auto!">
          <RbAlert className="rb:text-[14px] rb:py-2.5! rb:px-3! rb:leading-4">{suggestions.health_summary}</RbAlert>
          
          {suggestions.suggestions.map((item, index) => (
            <div key={index} className="rb:leading-5">
              <div className="rb:font-medium rb:mb-2">{index + 1}. {item.title}</div>

              <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3">
                <div className="rb:mb-2">{item.content}</div>

                <ul className="rb:list-disc rb:ml-4">
                  {item.actionable_steps.map((vo, idx) => <li key={idx}>{vo}</li>)}
                </ul>
              </div>
            </div>
          ))}
        </Flex>
        : <Empty size={88} subTitle={t(loading ? 'statementDetail.suggestionLoading' : 'empty.tableEmpty')} className="rb:h-full" />
      }
    </RbCard>
  )
})

export default Suggestions