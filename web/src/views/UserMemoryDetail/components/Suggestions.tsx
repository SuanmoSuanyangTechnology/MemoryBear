/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:31:50 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:31:50 
 */
import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

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
const Suggestions = forwardRef<{ handleRefresh: () => void; }>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState(false)
  const [suggestions, setSuggestions] = useState<Suggestions | null>(null)

  useEffect(() => {
    getSuggestionData()
  }, [id])

  const getSuggestionData = () => {
    if (!id) {
      return
    }
    setLoading(true)
    getEmotionSuggestions(id)
      .then((res) => {
        setSuggestions(res as Suggestions)
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