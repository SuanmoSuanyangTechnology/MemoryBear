import { type FC, type ReactNode, useEffect, useRef, useState } from 'react'
import { Flex } from 'antd'
import clsx from 'clsx'
import ChatInput from './ChatInput'
import type { TestParams } from '../index'
import dayjs from 'dayjs'
import Markdown from '@/components/Markdown'

interface ChatProps {
  empty?: ReactNode;
  data: ChatItem[];
  query?: TestParams;
  onChange: (query: TestParams) => void;
  onSend: () => void;
  loading: boolean;
  source?: 'conversation' | 'memory';
}
export interface ChatItem {
  id?: string;
  conversation_id?: string | null;
  role?: 'user' | 'assistant';
  content?: string;
  message?: string;
  created_at?: number | string;
  meta_data?: Record<string, string | number>[];
}

const Chat: FC<ChatProps> = ({ empty, data, query, onChange, onSend, loading, source = 'memory' }) => {
  const scrollContainerRefs = useRef<(HTMLDivElement | null)>(null)
  const [isMemory, setIsMemory] = useState<boolean>(source === 'memory')

  useEffect(() => {
    setIsMemory(source === 'memory')
  }, [source])
  useEffect(() => {
    setTimeout(() => {
      if (scrollContainerRefs.current) {
        scrollContainerRefs.current.scrollTop = scrollContainerRefs.current.scrollHeight;
      }
    }, 0);
  }, [data])
  
  return (
    <div className="rb:h-full rb:relative rb:pt-[8px]">
      {data.length === 0 ? (
        <Flex vertical justify="space-between" className="rb:h-full rb:w-full rb:relative">
          {/* Empty */}
          <div className="rb:h-[calc(100%-144px)] rb:overflow-y-auto rb:overflow-x-hidden rb:flex rb:items-center rb:justify-center">
            {empty}
          </div>

          <ChatInput source={source} query={query} onChange={onChange} onSend={onSend} loading={loading} />
        </Flex>
      )
      : (
        <div ref={scrollContainerRefs} className={clsx("rb:relative rb:overflow-y-auto", {
          'rb:h-[calc(100%-152px)]': !isMemory,
          'rb:h-[calc(100vh-362px)]': isMemory
        })}>
          {data.map((item, index) => (
            <div key={index} className={clsx("rb:relative", {
              'rb:mt-[24px]': index !== 0,
              'rb:right-[0] rb:text-right': item.role === 'user',
              'rb:left-[0] rb:text-left': item.role === 'assistant',
            })}>
              <div className={clsx('rb:border rb:text-left rb:rounded-[8px] rb:mt-[6px] rb:leading-[18px] rb:p-[10px_12px_2px_12px] rb:inline-block rb:max-w-[400px]', {
                'rb:bg-[rgba(21,94,239,0.08)] rb:border-[rgba(21,94,239,0.30)]': item.role === 'user',
                'rb:bg-[#FFFFFF] rb:border-[#EBEBEB]': item.role === 'assistant',
              })}>
                <Markdown content={item.content || ''} />
              </div>
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-[16px] rb:font-regular">{dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}</div>
            </div>
          ))}
        </div>
      )}

      <ChatInput source={source} query={query} onChange={onChange} onSend={onSend} loading={loading} />
    </div>
  )
}
export default Chat
