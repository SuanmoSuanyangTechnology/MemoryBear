/*
 * Perceptual memory node list
 * Shared by the process-data and final-result panels; shows images/audio/video/files with their summary, topic, domain and keywords
 */
import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Flex, Image } from 'antd'
import AudioPlayer from '@/views/UserMemoryDetail/components/AudioPlayer'
import VideoPlayer from '@/views/UserMemoryDetail/components/VideoPlayer'
import Empty from '@/components/Empty'

/** Perceptual memory display fields (see perceptual_retrieve on the conversation page) */
const PERCEPTUAL_FIELDS = ['summary', 'topic', 'domain', 'keywords'] as const

interface PerceptualNodesProps {
  nodes: any[];
}

const PerceptualNodes: FC<PerceptualNodesProps> = ({ nodes }) => {
  const { t } = useTranslation()

  const handleDownload = (file_path?: string) => {
    if (!file_path) return
    window.open(file_path, '_blank')
  }

  if (!nodes || nodes.length === 0) return null

  return (
    <Flex vertical gap={12} className="rb:mb-2!">
      {nodes.map((node: any, index: number) => (
        <div key={index} className="rb:p-3 rb:bg-white rb:rounded-xl">
          {node.url
            ? <>
              {/(jpg|jpeg|png|gif|webp|svg)$/i.test(node.file_type)
                ? <Image src={node.url} alt={node.file_name} className="rb:rounded-xl rb:h-45!" />
                : /(mp4|webm|ogg|mov)$/i.test(node.file_type)
                ? <VideoPlayer src={node.url} />
                : /(mp3|wav|ogg|m4a|aac|mpeg)$/i.test(node.file_type)
                ? <AudioPlayer src={node.url} fileName={node.file_name} fileSize='-' />
                : <Flex gap={11} align="center" justify="space-between" className="rb:bg-[#F6F6F6] rb:min-h-15.5! rb:rounded-xl rb:p-3!">
                  <Flex gap={12} align="center">
                    <div className="rb:w-7.5 rb:h-9 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/file.svg')]"></div>
                    <div>
                      <div className="rb:leading-5 rb:font-medium rb:mb-1 rb:wrap-break-word rb:line-clamp-1">{node.file_name}</div>
                      <div className="rb:text-[#5B6167] rb:leading-4.5 rb:text-[12px]">
                        {node.file_type}
                      </div>
                    </div>
                  </Flex>
                  <div
                    className="rb:size-6 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/userMemory/download.svg')] rb:hover:bg-[url('@/assets/images/userMemory/download_hover.svg')]"
                      onClick={() => handleDownload(node.url)}
                  ></div>
                </Flex>
              }
            </>
            : <div className="rb:bg-[#F6F6F6] rb:min-h-15.5! rb:rounded-xl rb:p-3!">
              <Empty size={44} />
            </div>
          }
          <Flex vertical gap={12}>
            {PERCEPTUAL_FIELDS.map(key => {
              const value = node[key]
              if (value == null || (Array.isArray(value) && value.length === 0)) return null
              return (
                <div key={key} className="rb:leading-5">
                  <div className="rb:text-[#5B6167] rb:mb-1">{t(`perceptualDetail.${key}`)}</div>
                  {Array.isArray(value)
                    ? <Flex wrap gap={8}>
                        {value.map((item: string, i: number) => (
                          <div key={i} className="rb:bg-[#F6F6F6] rb:rounded-[13px] rb:text-[12px] rb:py-1 rb:px-2 rb:leading-4.5">{item}</div>
                        ))}
                      </Flex>
                    : <div className="rb:text-[#212332]">{value}</div>
                  }
                </div>
              )
            })}
          </Flex>
        </div>
      ))}
    </Flex>
  )
}

export default PerceptualNodes
