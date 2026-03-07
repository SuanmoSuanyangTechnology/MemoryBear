/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:17 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-25 19:04:55
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
  const prevDataLengthRef = useRef(data.length);
  const isScrolledToBottomRef = useRef(true); // Track if user is scrolled to bottom
  
  // Track scroll position to determine if user is at bottom
  useEffect(() => {
    const handleScroll = () => {
      if (scrollContainerRef.current) {
        const { scrollTop, scrollHeight, clientHeight } = scrollContainerRef.current;
        // Consider user is at bottom if within 20px of the bottom
        isScrolledToBottomRef.current = scrollHeight - scrollTop - clientHeight < 20;
      }
    };
    
    const container = scrollContainerRef.current;
    if (container) {
      container.addEventListener('scroll', handleScroll);
      // Initial check
      handleScroll();
    }
    
    return () => {
      if (container) {
        container.removeEventListener('scroll', handleScroll);
      }
    };
  }, []);
  
  // Auto-scroll to bottom when data changes to show latest messages
  // When data array length remains unchanged, if data is updated and user manually scrolled up, don't auto-scroll to bottom
  // When data array length changes, auto-scroll to bottom
  // If already scrolled to bottom, will auto-scroll to bottom
  useEffect(() => {
    setTimeout(() => {
      if (scrollContainerRef.current) {
        // Auto-scroll if data length changed OR user is currently at bottom
        if (data.length !== prevDataLengthRef.current || isScrolledToBottomRef.current) {
          scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
        }
        prevDataLengthRef.current = data.length;
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
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:font-regular rb:px-1">
                    {labelFormat(item)}
                  </div>
                }
                {/* Message bubble */}
                <div className={clsx('rb:text-left rb:rounded-lg rb:leading-5 rb:p-[10px_12px_2px_12px] rb:inline-block rb:max-w-130 rb:wrap-break-word rb:relative', contentClassNames, {
                  // Error message style (content is null and not assistant message)
                  'rb:bg-[rgba(255,93,52,0.08)] rb:text-[#FF5D34]': (item.status && item.status !== 'completed') || (errorDesc && item.role === 'assistant' && item.content === null && !renderRuntime),
                  // Assistant message style
                  'rb:bg-[#E3EBFD]': item.role === 'user',
                  // User message style
                  'rb:bg-[#F6F6F6] rb:text-[#212332]': item.role === 'assistant' && (item.content || item.content === '' || typeof renderRuntime === 'function'),
                  'rb:mt-1.5': labelPosition === 'top',
                  'rb:mb-1.5': labelPosition === 'bottom',
                })}>
                  {item.status && <div className="rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/conversation/exclamation_circle.svg')] rb:absolute rb:-left-7"></div>}
                  {item.subContent && renderRuntime && renderRuntime(item, index)}
                  {/* Render message content using Markdown component */}
                  <Markdown content={renderRuntime ? item.content ?? '' : item.content ?? errorDesc ?? ''} />
                </div>
                {/* Bottom label (such as timestamp, username, etc.) */}
                {labelPosition === 'bottom' &&
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:font-regular">
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
