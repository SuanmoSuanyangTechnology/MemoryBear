/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:23 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:32:23 
 */
import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Space, Tooltip, Image } from 'antd';

import RbCard from '@/components/RbCard/Card'
import {
  getPerceptualLastVisual,
  getPerceptualLastListen,
  getPerceptualLastText,
} from '@/api/memory'

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
const KEYS = {
  last_visual: ['summary', 'keywords', 'topic', 'domain', 'scene'],
  last_listen: ['summary', 'keywords', 'topic', 'domain', 'speaker_count'],
  last_text: ['summary', 'keywords', 'topic', 'domain', 'section_count'],
}

/**
 * PerceptualLastInfo Component
 * Displays the last perceptual memory (visual, audio, or text)
 * Shows file preview and metadata based on perceptual type
 */
const PerceptualLastInfo: FC<{ type: 'last_visual' | 'last_listen' | 'last_text' }> = ({ type }) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<PerceptualLastInfoItem>({} as PerceptualLastInfoItem)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id, type])
  const getData = () => {
    if (!id || !type) return
    setLoading(true)
    const request = type === 'last_visual'
      ? getPerceptualLastVisual(id)
      : type === 'last_listen'
      ? getPerceptualLastListen(id)
      : getPerceptualLastText(id)
    request.then((res) => {
      const response = res as PerceptualLastInfoItem
      setData(response)
      setLoading(false) 
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
    >
      {loading
        ? <Skeleton active />
        : <div>
            <div className="rb:bg-[#F0F3F8] rb:h-36 rb:rounded-sm rb:flex rb:items-center rb:justify-center rb:overflow-hidden">
              {data.file_path ? (
                type === 'last_visual' ? (
                  /\.(mp4|webm|ogg|mov)$/i.test(data.file_name) ? (
                    <video controls className="rb:max-w-full rb:max-h-full">
                      <source src={data.file_path} />
                    </video>
                  ) : /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(data.file_name) ? (
                    <Image src={data.file_path} alt={data.file_name} />
                    // <img src={data.file_path} alt={data.file_name} className="rb:max-w-full rb:max-h-full rb:object-contain" />
                  ) : (
                    <div className="rb:text-[#5B6167]">{data.file_name}</div>
                  )
                ) : type === 'last_listen' && /\.(mp3|wav|ogg|m4a|aac)$/i.test(data.file_name) ? (
                  <audio controls className="rb:w-full">
                    <source src={data.file_path} />
                  </audio>
                ) : (
                  <div className="rb:text-[#5B6167] rb:cursor-pointer" onClick={handleDownload}>{data.file_name}</div>
                )
              ) : (
                <div className="rb:text-[#5B6167]">{t('empty.tableEmpty')}</div>
              )}
            </div>
            <Space size={4} direction="vertical" className="rb:w-full rb:mt-3">
              {KEYS[type].map(key => {
                const value = (data as any)[key]
                return (
                  <div key={key} className="rb:flex rb:justify-between rb:items-center rb:gap-3">
                    <div className="rb:text-[#5B6167]">{t(`perceptualDetail.${key}`)}</div>
                    {key === 'summary' ? (
                      <Tooltip title={value}>
                        <div className="rb:flex-1 rb:text-right rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">
                          {typeof value === 'string' ? value : Array.isArray(value) ? value.join('、') : '-'}
                        </div>
                      </Tooltip>
                      )
                      : <div className="rb:flex-1 rb:text-right">
                        {typeof value === 'string' ? value : Array.isArray(value) ? value.join('、') : '-'}
                      </div>
                    }
                  </div>
                )
              })}
          </Space>
          </div>
        }
    </RbCard>
  )
}
export default PerceptualLastInfo