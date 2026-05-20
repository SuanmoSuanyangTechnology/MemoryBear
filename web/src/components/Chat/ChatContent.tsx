/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:17 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-18 12:14:34
 */
import { type FC, useRef, useEffect, useState } from 'react'
import clsx from 'clsx'

import { Spin, Flex, Button } from 'antd'
import { SoundOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

import Markdown from '@/components/Markdown'
import type { ChatContentProps, ChatItem } from './types'
import MessageFiles from './MessageFiles'

const getFileUrl = (file: any) => {
  return file.thumbUrl || file.url || (file.originFileObj ? URL.createObjectURL(file.originFileObj) : undefined)
}

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
  renderRuntime,
  onSend,
  userIcon,
  assistantIcon,
  isSupportTools = false,
  handleFeedback,
  isEnded = true,
}) => {
  const { t } = useTranslation()
  // Scroll container reference for controlling auto-scroll to bottom
  const scrollContainerRef = useRef<(HTMLDivElement | null)>(null)
  const prevDataLengthRef = useRef(data.length);
  const isScrolledToBottomRef = useRef(true);
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [expandedReasoning, setExpandedReasoning] = useState<Set<number>>(new Set())
  const [manualToggledReasoning, setManualToggledReasoning] = useState<Set<number>>(new Set())

  const toggleReasoning = (index: number) => {
    setManualToggledReasoning(prev => new Set(prev).add(index))
    setExpandedReasoning(prev => {
      const next = new Set(prev)
      next.has(index) ? next.delete(index) : next.add(index)
      return next
    })
  }

  const isReasoningExpanded = (index: number) => {
    if (manualToggledReasoning.has(index)) return expandedReasoning.has(index)
    return !data[index]?.content
  }
  const [playingIndex, setPlayingIndex] = useState<string | null>(null)

  const handlePlay = (audio_url: string, audio_status?: string) => {
    if (audio_status !== 'completed' && typeof audio_status === 'string') return
    if (playingIndex === audio_url) {
      audioRef.current?.pause()
      setPlayingIndex(null)
      return
    }
    if (audioRef.current) {
      audioRef.current.pause()
    }
    const audio = new Audio(audio_url)
    audioRef.current = audio
    audio.play()
    setPlayingIndex(audio_url)
    audio.onended = () => setPlayingIndex(null)
  }
  
  // Track scroll position to determine if user is at bottom
  useEffect(() => {
    const handleScroll = () => {
      if (scrollContainerRef.current) {
        const { scrollTop, scrollHeight, clientHeight } = scrollContainerRef.current;
        // Consider user is at bottom if within 100px of the bottom
        isScrolledToBottomRef.current = scrollHeight - scrollTop - clientHeight < 100;
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
    if (playingIndex && !data.some(item => item.meta_data?.audio_url === playingIndex)) {
      audioRef.current?.pause()
      setPlayingIndex(null)
    }
    setTimeout(() => {
      if (scrollContainerRef.current) {
        // Auto-scroll if data length changed OR user is currently at bottom
        if (data.length !== prevDataLengthRef.current || isScrolledToBottomRef.current) {
          scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
          isScrolledToBottomRef.current = true;
        }
        prevDataLengthRef.current = data.length;
      }
    }, 0);
  }, [data])

  const handleDownload = (file: any) => {
    window.open(getFileUrl(file), '_blank')
  }
  const onFormSubmit = (values: Record<string, any>) => {
    onSend?.(JSON.stringify(values))
  }

  const formatContent = (item: ChatItem) => {
    return renderRuntime && item.content && item.content.length > 0
      ? item.content
        : renderRuntime
      ? ''
        : item.meta_data?.error && item.meta_data?.error.length > 0
      ? item.meta_data?.error
        : item.content && item.content.length > 0
      ? item.content
        : errorDesc ?? ''
  }
  return (
    <div ref={scrollContainerRef} className={clsx("rb:relative rb:overflow-y-auto", classNames)}>
      {data.length === 0 
        ? empty // Display empty state
        : data.map((item, index) => {
          if (!item) return null
          return (
            <div key={index} className={clsx("rb:relative", {
              'rb:mt-6': index !== 0, // Add top margin for non-first messages
              'rb:right-0 rb:text-right': item.role === 'user', // User messages right-aligned
              'rb:left-0 rb:text-left': item.role === 'assistant', // Assistant messages left-aligned
            })}>
              {/* Don't display if streaming and content is empty */}
              {streamLoading && index === data.length - 1 && item.content === '' && !renderRuntime
                ? <Spin />
                : <>
                  <Flex
                    justify={item.role === 'user' ? "end" : "start"}
                    gap={12}
                  >
                    {/* Assistant icon */}
                    {item.role === 'assistant' && assistantIcon}
                    <div
                      className={item.role === 'assistant' && assistantIcon ? "rb:flex-1" : "rb:w-full!"}
                    >
                      {/* Top label (such as timestamp, username, etc.) */}
                      {labelPosition === 'top' &&
                        <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:font-regular rb:px-1">
                          {labelFormat(item)}
                        </div>
                      }
                      <MessageFiles files={item.meta_data?.files ?? []} contentClassNames={contentClassNames} onDownload={handleDownload} />
                      {/* Message bubble */}
                      <div className={clsx('rb:text-left rb:leading-5 rb:inline-block rb:wrap-break-word rb:relative', item.role === 'user' ? contentClassNames : '', {
                        // Error message style (content is null and not assistant message)
                        'rb:text-[#FF5D34]': (item.status && item.status !== 'completed') || (errorDesc && item.role === 'assistant' && item.content === null && !renderRuntime) || (item.role === 'assistant' && typeof item.meta_data?.error === 'string'),
                        // Assistant message style
                        'rb:bg-[#E3EBFD] rb:p-[10px_12px_2px_12px] rb:rounded-lg rb:max-w-130': item.role === 'user',
                        'rb:max-w-full rb:w-full': item.role === 'assistant',
                        // User message style
                        'rb:text-[#212332]': item.role === 'assistant' && (item.content || item.content === '' || typeof renderRuntime === 'function'),
                        'rb:mt-1': labelPosition === 'top',
                        'rb:mb-1': labelPosition === 'bottom',
                        'rb:pl-7': item.role === 'assistant' && typeof item.meta_data?.error === 'string',
                      })}>
                        {item.meta_data?.reasoning_content &&
                          <div className={clsx("rb:mb-4 rb-border rb:rounded-xl rb:px-4 rb:pt-4 rb:bg-white", {
                            'rb:hover:bg-[#F6F6F6] rb:w-64': !isReasoningExpanded(index)
                          })}>
                            <Flex
                              align="center"
                              justify="space-between"
                              className="rb:font-medium rb:pb-4!"
                            >
                              <span>{t('memoryConversation.reasoning_content')}</span>
                              <Flex
                                align="center"
                                justify="center"
                                className={clsx("rb:size-6.5 rb:cursor-pointer rb-border rb:rounded-lg", {
                                  'rb:hover:bg-[#F6F6F6]!': isReasoningExpanded(index)
                                })}
                                onClick={() => toggleReasoning(index)}
                              >
                                <div
                                  className={clsx("rb:size-4 rb:bg-cover", {
                                    'rb:bg-[url("@/assets/images/conversation/compress.svg")]': isReasoningExpanded(index),
                                    'rb:bg-[url("@/assets/images/conversation/expand.svg")]': !isReasoningExpanded(index)
                                  })}
                                ></div>
                            </Flex>
                            </Flex>
                          {isReasoningExpanded(index) && <Markdown content={item.meta_data.reasoning_content} className="rb:text-[#5B6167] rb:text-[12px]" />}
                          </div>
                        }
                        {((item.status && item.status !== 'completed') || typeof item.meta_data?.error === 'string') &&
                          <div className={clsx("rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/conversation/exclamation_circle.svg')] rb:absolute", {
                            'rb:-left-7!': item.status && item.status !== 'completed' && item.role === 'user',
                            'rb:left-0!': item.role === 'assistant' && typeof item.meta_data?.error === 'string',
                          })}></div>
                        }
                        {item.subContent && renderRuntime && renderRuntime(item, index)}
                        {/* Render message content using Markdown component */}
                        <Markdown
                          content={formatContent(item)}
                          onFormSubmit={onFormSubmit}
                        />

                        {item.meta_data?.citations && item.meta_data?.citations.length > 0 &&
                          <Flex vertical gap={4} className="rb:mt-1! rb:pt-3! rb-border-t rb:mb-2!">
                            <div className="rb:font-medium">{t('memoryConversation.citations')}</div>
                            {item.meta_data?.citations?.map((citation, idx) => (
                              <Flex key={idx} align="center" gap={12}>
                                <div
                                  className="rb:text-[#155EEF] rb:leading-5 rb:underline rb:cursor-pointer"
                                  onClick={() => {
                                    const params = new URLSearchParams({ documentId: citation.document_id, parentId: citation.knowledge_id });
                                    window.open(`/#/knowledge-base/${citation.knowledge_id}/DocumentDetails?${params}`, '_blank');
                                  }}
                                >{citation.file_name}</div>

                                {citation.download_url &&
                                  <div className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/application/export.svg')]"
                                    onClick={() => handleDownload({ url: citation.download_url })}
                                  ></div>
                                }
                              </Flex>
                            ))}
                          </Flex>
                        }
                      </div>
                      {/* Bottom label (such as timestamp, username, etc.) */}
                      {(labelPosition === 'bottom' || item.meta_data?.audio_url) &&
                        <Flex gap={16} align="center" justify={item.role === 'user' ? 'end' : 'start'}>
                          {labelPosition === 'bottom' &&
                            <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:font-regular">
                              {labelFormat(item)}
                            </div>
                          }
                          {item.meta_data?.audio_url && <>
                            {playingIndex !== item.meta_data?.audio_url && item.meta_data?.audio_status === 'pending'
                              ? <Spin />
                              : playingIndex !== item.meta_data?.audio_url
                                ? <SoundOutlined className={clsx("rb:cursor-pointer rb:size-5.5", {
                                  'rb:text-[#FF5D34]': item.meta_data?.audio_status === 'error',
                                  'rb:hover:text-[#155EEF]!': !item.meta_data?.audio_status || !['pending', 'error'].includes(item.meta_data?.audio_status)
                                })} onClick={() => handlePlay(item.meta_data?.audio_url!, item.meta_data?.audio_status)} />
                                : <div
                                  className="rb:size-5.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/audio_ing.gif')]"
                                  onClick={() => handlePlay(item.meta_data?.audio_url!, item.meta_data?.audio_status)}
                                />
                            }
                          </>}
                          {isSupportTools && item.role === 'assistant' && !(!isEnded && index === data.length - 1) && <>
                            <div
                              className={clsx("rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/like.svg')]", {
                                "rb:bg-[url('@/assets/images/conversation/like_active.svg')]": item.feedback_type === 'like',
                              })}
                              onClick={() => handleFeedback?.('like', item?.id)}
                            ></div>
                            <div
                              className={clsx("rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/like.svg')] rb:scale-y-[-1]", {
                                "rb:bg-[url('@/assets/images/conversation/like_active.svg')]": item.feedback_type === 'dislike',
                              })}
                              onClick={() => handleFeedback?.('dislike', item?.id)}
                            ></div>
                          </>}
                        </Flex>
                      }
                      {item.meta_data?.suggested_questions && item.meta_data?.suggested_questions?.length > 0 &&
                        <Flex wrap gap={8} className="rb:my-1!">
                          {item.meta_data?.suggested_questions?.map((question, idx) => (
                            <Button key={idx} size="small" className="rb:text-[12px]! rb:text-[#155EEF]!"
                              onClick={() => onSend?.(question)}
                            >{question}</Button>
                          ))}
                        </Flex>
                      }
                    </div>
                    {/* User icon */}
                    {item.role === 'user' && userIcon}
                  </Flex>
                </>
              }
            </div>
          )})
      }
    </div>
  )
}

export default ChatContent
