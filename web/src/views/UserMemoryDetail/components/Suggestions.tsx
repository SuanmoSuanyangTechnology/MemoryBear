import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import { getEmotionSuggestions } from '@/api/memory'
import RbAlert from '@/components/RbAlert'


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
const Suggestions: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [suggestions, setSuggestions] = useState<Suggestions | null>(null)

  useEffect(() => {
    getSuggestionData()
  }, [id])

  const getSuggestionData = () => {
    if (!id) {
      return
    }
    getEmotionSuggestions(id)
      .then((res) => {
        setSuggestions(res as Suggestions)
      })
  }

  return (
    <RbCard
      title={t('statementDetail.suggestions')}
      headerType="borderless"
      headerClassName="rb:text-[18px]! rb:leading-[24px]"
    >
      {suggestions?.suggestions && suggestions?.suggestions.length > 0
        ? <>
          <RbAlert className="rb:mb-3">{suggestions.health_summary}</RbAlert>
          {suggestions.suggestions.map((item, index) => (
            <div key={index} className="rb:mb-3">
              <div className="rb:font-medium">{index + 1}. {item.title}</div>
              <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-1 rb:mb-2">{item.content}</div>
              {item.actionable_steps.map((vo, idx) => <div key={idx} className="rb:ml-6 rb:text-[12px] rb:text-[#5B6167] rb:mt-1">- {vo}</div>)}
            </div>
          ))}
        </>
        : <Empty size={88} className="rb:h-full" />
      }
    </RbCard>
  )
}

export default Suggestions