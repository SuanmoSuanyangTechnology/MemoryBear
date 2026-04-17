/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:34:04 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-10 16:32:52
 */
import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Divider, Flex } from 'antd'
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import PageScrollList from '@/components/PageScrollList'
import Markdown from '@/components/Markdown'
import { getRagContentUrl } from '@/api/memory'

interface DataItem {
  role: 'user' | 'assistant';
  content: string;
}

const ConversationMemory: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()

  return (
    <RbCard
      title={<span className="rb:font-[MiSans-Bold] rb:font-bold">{t('userMemory.conversationMemory')}</span>}
      headerType="borderless"
      headerClassName="rb:min-h-[54px]! rb:pt-0! rb:mb-0!"
      bodyClassName="rb:p-4! rb:pt-0! rb:pb-1! rb:h-[calc(100%-54px)]!"
      className="rb:h-full!"
    >
      <PageScrollList<DataItem>
        url={getRagContentUrl}
        query={{ end_user_id: id }}
        column={1}
        gutter={0}
        renderItem={(item, index) => (
          <div>
            {index !== 0 && <Divider className="rb:mt-1! rb:mb-3! rb:ml-11!" />}
            <Flex
              align="start"
              gap={12}
            >
              <div className={clsx("rb:size-8 rb:bg-cover", {
                'rb:bg-[url(@/assets/images/conversation/user.png)]': item.role === 'user',
                'rb:bg-[url(@/assets/images/conversation/ai.png)]': item.role === 'assistant',
              })}></div>
              <div
                className="rb:flex-1"
              >
                <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4.5 rb:mb-0.5">
                  {item.role === 'assistant' ? t('userMemory.assistant') : t('userMemory.user')}
                </div>
                <Markdown content={item.content} />
              </div>
            </Flex>
          </div>
        )}
        className="rb:h-full!"
      />
    </RbCard>
  )
}

export default ConversationMemory
