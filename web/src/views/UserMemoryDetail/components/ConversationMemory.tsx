/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:34:04 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-12 18:34:52
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
      headerClassName="rb:text-[18px]! rb:leading-[24px]"
      bodyClassName="rb:h-[calc(100%-56px)]! rb:overflow-hidden"
      className="rb:h-[calc(100vh-104px)]!"
    >
      <PageScrollList<string>
        url={getRagContentUrl}
        query={{ end_user_id: id }}
        column={1}
        renderItem={(item) => (
          <div className="rb:rounded-lg rb:border rb:border-[#DFE4ED] rb:px-4 rb:py-3 rb:bg-[#F0F3F8] rb:text-gray-800 rb:text-sm">
            <Markdown content={item} />
          </div>
        )}
        className="rb:h-full!"
        // className="rb:h-[calc(100%-24px)]!"
      />
    </RbCard>
  )
}

export default ConversationMemory
