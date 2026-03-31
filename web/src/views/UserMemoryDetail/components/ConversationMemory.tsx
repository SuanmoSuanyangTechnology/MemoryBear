/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:34:04 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 10:28:53
 */
import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import RbCard from '@/components/RbCard/Card'
import PageScrollList from '@/components/PageScrollList'
import Markdown from '@/components/Markdown'
import { getRagContentUrl } from '@/api/memory'

const ConversationMemory: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()

  return (
    <RbCard
      title={t('userMemory.conversationMemory')}
      headerType="borderless"
      headerClassName="rb:min-h-[54px]! rb:pt-0! rb:mb-0! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:p-4! rb:pt-0! rb:h-[calc(100%-54px)]!"
      className="rb:h-full!"
    >
      <PageScrollList<string>
        url={getRagContentUrl}
        query={{ end_user_id: id }}
        column={1}
        renderItem={(item: string) => (
          <div
            className="rb:rounded-lg rb-border rb:px-4 rb:py-3 rb:bg-[#F0F3F8] rb:mt-2 rb:text-[#212332] rb:text-sm"
          >
            <Markdown content={item} />
          </div>
        )}
        className="rb:h-full!"
      />
    </RbCard>
  )
}

export default ConversationMemory
