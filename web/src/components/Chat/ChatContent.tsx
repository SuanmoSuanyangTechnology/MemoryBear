/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:17 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-06 21:05:52
 */
import { type FC, useRef, useEffect } from 'react'
import clsx from 'clsx'
import Markdown from '@/components/Markdown'
import type { ChatContentProps } from './types'
import { Spin } from 'antd'

/**
 * Chat Content Display Component
 * Responsible for rendering chat message list, supports different role message styles and auto-scrolling
 */
const ChatContent: FC<ChatContentProps> = ({
  classNames,
  contentClassNames,
  data = [],
  streamLoading = false,
  empty,
  labelPosition = 'bottom',
  labelFormat,
  errorDesc,
  renderRuntime
}) => {
  // Scroll container reference for controlling auto-scroll to bottom
  const scrollContainerRef = useRef<(HTMLDivElement | null)>(null)
  
  // Auto-scroll to bottom when data changes to show latest messages
  useEffect(() => {
    setTimeout(() => {
      if (scrollContainerRef.current) {
        scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
      }
    }, 0);
  }, [data])
  return (
    <div ref={scrollContainerRef} className={clsx("rb:relative rb:overflow-y-auto", classNames)}>
      {data.length === 0 
        ? empty // Display empty state
        : data.map((item, index) => (
          <div key={index} className={clsx("rb:relative", {
            'rb:mt-6': index !== 0, // Add top margin for non-first messages
            'rb:right-0 rb:text-right': item.role === 'user', // User messages right-aligned
            'rb:left-0 rb:text-left': item.role === 'assistant', // Assistant messages left-aligned
          })}>
            {/* Don't display if streaming and content is empty */}
            {streamLoading && item.content === '' && !renderRuntime
              ? <Spin />
              : <>
                {/* Top label (such as timestamp, username, etc.) */}
                {labelPosition === 'top' &&
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:font-regular">
                    {labelFormat(item)}
                  </div>
                }
                {/* Message bubble */}
                <div className={clsx('rb:border rb:text-left rb:rounded-lg rb:mt-1.5 rb:leading-4.5 rb:p-[10px_12px_2px_12px] rb:inline-block rb:max-w-130 rb:wrap-break-word', contentClassNames, {
                  // Error message style (content is null and not assistant message)
                  'rb:border-[rgba(255,93,52,0.30)] rb:bg-[rgba(255,93,52,0.08)] rb:text-[#FF5D34]': errorDesc && item.role === 'assistant' && item.content === null && !renderRuntime,
                  // Assistant message style
                  'rb:bg-[rgba(21,94,239,0.08)] rb:border-[rgba(21,94,239,0.30)]': item.role === 'user',
                  // User message style
                  'rb:bg-[#FFFFFF] rb:border-[#EBEBEB]': item.role === 'assistant' && (item.content || item.content === '' || typeof renderRuntime === 'function'),
                })}>
                  {item.subContent && renderRuntime && renderRuntime(item, index)}
                  {/* Render message content using Markdown component */}
                  <Markdown content={renderRuntime ? item.content ?? '' : item.content ?? errorDesc ?? ''} />
                </div>
                {/* Bottom label (such as timestamp, username, etc.) */}
                {labelPosition === 'bottom' &&
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:font-regular rb:mt-2">
                    {labelFormat(item)}
                  </div>
                }
              </>
            }
          </div>
        ))
      }
    </div>
  )
}

export default ChatContent
