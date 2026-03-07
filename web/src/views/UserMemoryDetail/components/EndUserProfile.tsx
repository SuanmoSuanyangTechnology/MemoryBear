/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:33:30 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-11 14:51:00
 */
/**
 * End User Profile Component
 * Displays and manages end user profile information
 */

import { forwardRef, useImperativeHandle, useEffect, useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Flex } from 'antd';
import dayjs from 'dayjs'
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import {
  getEndUserProfile,
} from '@/api/memory'
import EndUserProfileModal from './EndUserProfileModal'
import type { EndUser, EndUserProfileModalRef, EndUserProfileRef } from '../types'

/**
 * Component props
 */
interface EndUserProfileProps {
  onDataLoaded?: (data: { other_name?: string; id: string }) => void;
  className?: string;
}

const EndUserProfile = forwardRef<EndUserProfileRef, EndUserProfileProps>(({ onDataLoaded, className }, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const endUserProfileModalRef = useRef<EndUserProfileModalRef>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<EndUser | null>(null)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  /** Fetch profile data */
  const getData = () => {
    if (!id) return
    setLoading(true)
    getEndUserProfile(id).then((res) => {
      const userData = res as EndUser
      setData(userData)
      onDataLoaded?.({
        other_name: userData.other_name,
        id: userData.id
      })
      setLoading(false) 
    })
    .finally(() => {
      setLoading(false)
    })
  }
  /** Format profile items for display */
  const formatItems = useCallback(() => {
    return ['other_name', 'position', 'department', 'contact', 'phone', 'hire_date'].map(key => ({
      key,
      label: t(`userMemory.${key}`),
      children: key === 'hire_date' && data?.[key] ? dayjs(data[key as keyof EndUser]).format('YYYY-MM-DD') : String(data?.[key as keyof EndUser] || '-'),
    }))
  }, [data])
  /** Open edit modal */
  const handleEdit = () => {
    if (!data) return
    endUserProfileModalRef.current?.handleOpen(data)
  }

  useImperativeHandle(ref, () => ({
    data
  }));

  return (
    <RbCard 
      title={t('userMemory.endUserProfile')} 
      extra={
        <div 
          className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]" 
          onClick={handleEdit}
        ></div>
      }
      headerClassName="rb:min-h-[46px]!! rb:font-medium!"
      className={clsx("rb:bg-[#FFFFFF]! rb:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.13)]! rb:absolute! rb:w-80 rb:top-29 rb:left-26", className)}
      bodyClassName="rb:px-5! rb:pb-5! rb:pt-3.75! rb:max-h-[calc(100vh-176px)] rb:overflow-auto"
    >
      {loading
        ? <Skeleton />
        : <Flex vertical gap={20}>
            {formatItems().map(vo => (
              <div key={vo.key} className="rb:leading-5">
                <div className="rb:text-[#7B8085]">{vo.label}</div>
                <div className="rb:mt-0.5">{vo.children}</div>
              </div>
            ))}

            <div className="rb:text-[#7B8085] rb:text-[12px] rb:leading-4.5">
              {t('userMemory.updated_at')}: {data?.updatetime_profile ? dayjs(data?.updatetime_profile).format('YYYY/MM/DD HH:mm:ss') : ''}
            </div>
        </Flex>
      }
      <EndUserProfileModal
        ref={endUserProfileModalRef}
        refresh={getData}
      />
    </RbCard>
  )
})
export default EndUserProfile