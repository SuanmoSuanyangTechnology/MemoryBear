import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton } from 'antd';
import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty';
import { List } from 'antd';
import Markdown from '@/components/Markdown'
import {
  getRagContent
} from '@/api/memory'

const ConversationMemory:FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(true)
  const [list, setList] = useState<string[]>([])

  useEffect(() => {
    if (!id) return
    getList()
  }, [id])
  const getList = () => {
    if (!id) return
    setLoading(true)
    getRagContent(id).then((res) => {
      setList((res as { contents?: [] }).contents || [])
    })
    .finally(() => {
      setLoading(false)
    })
  }

  return (
    <RbCard 
      title={t('userMemory.conversationMemory')}
      headerClassName="rb:text-[18px]! rb:leading-[24px]"
      bodyClassName="rb:h-[100%]! rb:overflow-hidden rb:py-0!"
    >
      {loading
        ? <Skeleton />
        : list.length > 0
        ? <List
            dataSource={list}
            grid={{ gutter: 12, column: 1 }}
            renderItem={(item, index) => (
              <List.Item>
                <div
                  key={index}
                  className="rb:rounded-lg rb:border rb:border-[#DFE4ED] rb:px-4 rb:py-3 rb:bg-[#F0F3F8] rb:mt-2 rb:text-gray-800 rb:text-sm"
                >
                  <Markdown content={item} />
                </div>
              </List.Item>
            )}
          />
        : <Empty className="rb:h-full" />
      }
    </RbCard>
  )
}
export default ConversationMemory