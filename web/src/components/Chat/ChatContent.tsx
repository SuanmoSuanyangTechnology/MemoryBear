/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:17 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2025-12-11 13:40:18
 */
import { type FC, useRef, useEffect } from 'react'
import clsx from 'clsx'
import Markdown from '@/components/Markdown'
import type { ChatContentProps } from './types'

/**
 * 聊天内容显示组件
 * 负责渲染聊天消息列表，支持不同角色的消息样式和自动滚动
 */
const ChatContent: FC<ChatContentProps> = ({
  classNames,
  contentClassNames,
  data = [],
  streamLoading = false,
  empty,
  labelPosition = 'bottom',
  labelFormat,
  errorDesc
}) => {
  // 滚动容器引用，用于控制自动滚动到底部
  const scrollContainerRef = useRef<(HTMLDivElement | null)>(null)
  
  // 当数据变化时，自动滚动到底部显示最新消息
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
        ? empty // 显示空状态
        : data.map((item, index) => (
          <div key={index} className={clsx("rb:relative", {
            'rb:mt-6': index !== 0, // 非第一条消息添加上边距
            'rb:right-0 rb:text-right': item.role === 'user', // 用户消息右对齐
            'rb:left-0 rb:text-left': item.role === 'assistant', // 助手消息左对齐
          })}>
            {/* 流式加载时且内容为空则不显示 */}
            {streamLoading && item.content === ''
              ? null
              : <>
                {/* 顶部标签（如时间戳、用户名等） */}
                {labelPosition === 'top' &&
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:font-regular">
                    {labelFormat(item)}
                  </div>
                }
                {/* 消息气泡框 */}
                <div className={clsx('rb:border rb:text-left rb:rounded-lg rb:mt-1.5 rb:leading-4.5 rb:p-[10px_12px_2px_12px] rb:inline-block rb:max-w-100', contentClassNames, {
                  // 错误消息样式（内容为null且非助手消息）
                  'rb:border-[rgba(255,93,52,0.30)] rb:bg-[rgba(255,93,52,0.08)] rb:text-[#FF5D34]': errorDesc && item.role === 'assistant' && item.content === null,
                  // 助手消息样式
                  'rb:bg-[rgba(21,94,239,0.08)] rb:border-[rgba(21,94,239,0.30)]': item.role === 'user',
                  // 用户消息样式
                  'rb:bg-[#FFFFFF] rb:border-[#EBEBEB]': item.role === 'assistant' && (item.content || item.content === ''),
                })}>
                  {/* 使用Markdown组件渲染消息内容 */}
                  <Markdown content={item.content ?? errorDesc ?? ''} />
                </div>
                {/* 底部标签（如时间戳、用户名等） */}
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
