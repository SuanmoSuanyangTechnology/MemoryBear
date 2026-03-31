/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:23 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 14:57:34
 */
import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Image, Flex } from 'antd';
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import AudioPlayer from './AudioPlayer'
import VideoPlayer from './VideoPlayer'
import {
  getPerceptualLastVisual,
  getPerceptualLastListen,
  getPerceptualLastText,
} from '@/api/memory'
import Empty from '@/components/Empty';

/**
 * Perceptual last info item structure
 * @property {string} id - Item ID
 * @property {string} file_name - File name
 * @property {string} file_ext - File extension
 * @property {string} file_path - File path URL
 * @property {number} storage_type - Storage type
 * @property {string} summary - Content summary
 * @property {string[]} keywords - Keywords
 * @property {string} topic - Topic
 * @property {string} domain - Domain
 * @property {number | string} created_time - Creation time
 * @property {string[]} scene - Scene information
 * @property {number} speaker_count - Speaker count
 * @property {number} section_count - Section count
 */
interface PerceptualLastInfoItem {
  id: string;
  file_name: string;
  file_ext: string;
  file_path: string;
  storage_type: number;
  summary: string;
  keywords: string[];
  topic: string;
  domain: string;
  created_time: number | string;
  scene: string[]
  speaker_count: number;
  section_count: number;
}

/**
 * Field keys for different perceptual types
 */
const KEYS: Record<string, string[]> = {
  last_visual: ['summary', 'keywords', 'topic', 'domain', 'scene'],
  last_listen: ['summary', 'keywords', 'topic', 'domain', 'speaker_count'],
  last_text: ['summary', 'keywords', 'topic', 'domain', 'section_count'],
}

/**
 * PerceptualLastInfo Component
 * Displays the last perceptual memory (visual, audio, or text)
 * Shows file preview and metadata based on perceptual type
 */
const PerceptualLastInfo: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<PerceptualLastInfoItem>({} as PerceptualLastInfoItem)
  const [type, setType] = useState('last_visual')
  const [fileSize, setFileSize] = useState<string>('')

  useEffect(() => {
    if (!id) return
    getData()
  }, [id, type])
  const getData = () => {
    if (!id || !type) return
    setLoading(true)
    setFileSize('')
    const request = type === 'last_visual'
      ? getPerceptualLastVisual(id)
      : type === 'last_listen'
      ? getPerceptualLastListen(id)
      : getPerceptualLastText(id)
    request.then((res) => {
      const response = res as PerceptualLastInfoItem
      setData(response)
      setLoading(false)
      if (response.file_path) {
        fetch(response.file_path, { method: 'GET' })
          .then(r => {
            const bytes = Number(r.headers.get('content-length'))
            if (!bytes) return
            setFileSize(bytes < 1024 * 1024
              ? `${(bytes / 1024).toFixed(1)} KB`
              : `${(bytes / 1024 / 1024).toFixed(1)} MB`)
          })
          .catch(() => {})
      }
    })
    .finally(() => {
      setLoading(false)
    })
  }

  const handleDownload = () => {
    if (!data.file_path) return
    window.open(data.file_path, '_blank')
  }

  return (
    <RbCard
      title={t(`perceptualDetail.${type}`)}
      headerType="borderless"
      headerClassName="rb:min-h-[50px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:p-4! rb:pt-0! rb:h-[calc(100%-50px)] rb:overflow-y-auto"
      className="rb:h-full! rb:w-full!"
    >
      <Flex align="center" gap={8} className="rb:mb-4!">
        {Object.keys(KEYS).map(key => (
          <div
            key={key}
            className={clsx("rb:text-[12px] rb:rounded-[14px] rb:py-1 rb:pl-2 rb:pr-3 rb:cursor-pointer", {
              'rb:bg-[#171719] rb:text-white': type === key,
              'rb:bg-[#F6F6F6]': type !== key
            })}
            onClick={() => setType(key)}
          >{t(`perceptualDetail.${key}`)}</div>))}
      </Flex>
      {loading
        ? <Skeleton active />
        : <Flex vertical gap={16} className="rb:w-108">
            {data.file_path
              ? <>
                {/\.(jpg|jpeg|png|gif|webp|svg)$/i.test(data.file_name)
                  ? <Image src={data.file_path} alt={data.file_name} width={432} className="rb:rounded-xl rb:h-45!" />
                  : /\.(mp4|webm|ogg|mov)$/i.test(data.file_name)
                  ? <VideoPlayer src={data.file_path} />
                  : /\.(mp3|wav|ogg|m4a|aac)$/i.test(data.file_name)
                  ? <AudioPlayer src={data.file_path} fileName={data.file_name} fileSize={fileSize} />
                  : <Flex gap={11} align="center" justify="space-between" className="rb:bg-[#F6F6F6] rb:min-h-15.5! rb:rounded-xl rb:p-3!">
                    <Flex gap={12} align="center">
                      <div className="rb:w-7.5 rb:h-9 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/file.svg')]"></div>
                      <div>
                        <div className="rb:leading-5 rb:font-medium rb:mb-1 rb:wrap-break-word rb:line-clamp-1">{data.file_name}</div>
                        <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">
                          {fileSize || '-'}
                        </div>
                      </div>
                    </Flex>
                    <div
                      className="rb:size-6 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/userMemory/download.svg')] rb:hover:bg-[url('@/assets/images/userMemory/download_hover.svg')]"
                      onClick={handleDownload}
                    ></div>
                  </Flex>
                }
              </>
              : <div className="rb:bg-[#F6F6F6] rb:min-h-15.5! rb:rounded-xl rb:p-3!">
                <Empty size={44} />
              </div>
            }
            {KEYS[type].map(key => {
              const value = (data as any)[key]
              return (
                <div key={key} className="rb:leading-5">
                  <div className="rb:text-[#5B6167] rb:mb-1">{t(`perceptualDetail.${key}`)}</div>

                    {typeof value === 'string'
                      ? <div>{value}</div>
                      : Array.isArray(value)
                      ? <Flex wrap gap={11}>
                          {value.map((vo, index) => <div key={index} className="rb:bg-[#F6F6F6] rb:rounded-[13px] rb:py-1 rb:px-2 rb:text-[12px] rb:font-medium rb:leading-4.5">{vo}</div>)}
                        </Flex>
                      : '-'
                    }
                </div>
              )
            })}
        </Flex>
        }
    </RbCard>
  )
}
export default PerceptualLastInfo