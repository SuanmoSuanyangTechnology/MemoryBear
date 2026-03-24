/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:17 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-23 18:24:33
 */
import { type FC, useRef, useEffect, useState } from 'react'
import clsx from 'clsx'
import Markdown from '@/components/Markdown'
import type { ChatContentProps } from './types'
import { Spin, Divider, Space, Image, Flex } from 'antd'
import { SoundOutlined } from '@ant-design/icons'


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
  renderRuntime
}) => {
  // Scroll container reference for controlling auto-scroll to bottom
  const scrollContainerRef = useRef<(HTMLDivElement | null)>(null)
  const prevDataLengthRef = useRef(data.length);
  const isScrolledToBottomRef = useRef(true);
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [playingIndex, setPlayingIndex] = useState<number | null>(null)

  const handlePlay = (index: number, audio_url: string, audio_status?: string) => {
    if (audio_status !== 'completed' && !audio_status) return
    if (playingIndex === index) {
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
    setPlayingIndex(index)
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
                {item.meta_data?.files && item.meta_data?.files.length > 0 && <Flex gap={8} vertical align="end">
                  {item.meta_data?.files?.map((file) => {
                    if (file.type.includes('image')) {
                      return (
                        <div key={file.url || file.uid} className={`rb:inline-block rb:group rb:relative rb:rounded-lg ${contentClassNames}`}>
                          <Image src={getFileUrl(file)} alt={file.name} className="rb:w-full rb:max-w-80 rb:rounded-lg rb:object-cover rb:cursor-pointer" />
                        </div>
                      )
                    }
                    if (file.type.includes('video')) {
                      return (
                        <div key={file.url || file.uid} className="rb:inline-block rb:group rb:relative rb:rounded-lg">
                          <video src={getFileUrl(file)} controls className="rb:max-w-80 rb:rounded-lg rb:object-cover rb:cursor-pointer" />
                        </div>
                      )
                    }
                    if (file.type.includes('audio')) {
                      return (
                        <div key={file.url || file.uid} className="rb:inline-flex rb:items-center rb:group rb:relative rb:rounded-lg rb:bg-[#F0F3F8] rb:py-2 rb:px-2.5 rb:gap-2">
                          <audio src={getFileUrl(file)} controls className="rb:max-w-80" />
                        </div>
                      )
                    }
                    return (
                      <div key={file.url || file.uid} className="rb:relative rb:rounded-lg rb:bg-[#F0F3F8] rb:p-1! rb:cursor-pointer" onClick={() => handleDownload(file)}>
                        {(file.type.includes('excel') || file.type.includes('spreadsheetml.sheet') || file.type.includes('csv'))
                          ? <div
                            className="rb:size-10 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/excel.svg')]"
                          ></div>
                          :(file.type.includes('pdf'))
                          ? <div
                            className="rb:size-10 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/pdf.svg')]"
                          ></div>
                          : (file.type.includes('doc') || file.type.includes('docx') || file.type.includes('word') || file.type.includes('wordprocessingml.document'))
                          ? <div
                            className="rb:size-10 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/word.svg')]"
                          ></div>
                          : null
                        }
                      </div>
                    )
                  })}
                </Flex>}
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

                  {item.meta_data?.audio_url && <>
                    <Divider className="rb:my-3!" />
                    <Space size={12} className="rb:pb-2 rb:pl-1">
                      {playingIndex !== index && item.meta_data?.audio_status === 'pending'
                        ? <Spin />
                        : playingIndex !== index
                        ? <SoundOutlined className={clsx("rb:cursor-pointer rb:size-5.5", {
                          'rb:text-[#FF5D34]': item.meta_data?.audio_status === 'error',
                          'rb:hover:text-[#155EEF]!': !item.meta_data?.audio_status || !['pending', 'error'].includes(item.meta_data?.audio_status)
                        })} onClick={() => handlePlay(index, item.meta_data?.audio_url!, item.meta_data?.audio_status)} />
                        : <div
                            className="rb:size-5.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/audio_ing.gif')]"
                            onClick={() => handlePlay(index, item.meta_data?.audio_url!, item.meta_data?.audio_status)}
                          />
                      }
                    </Space>
                  </>}
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
