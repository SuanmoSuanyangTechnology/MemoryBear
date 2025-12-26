import { forwardRef, useImperativeHandle, useEffect, useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton } from 'antd';
import dayjs from 'dayjs'

import RbCard from '@/components/RbCard/Card'
import {
  getEndUserProfile,
} from '@/api/memory'
import EndUserProfileModal from './EndUserProfileModal'
import type { EndUser, EndUserProfileModalRef, EndUserProfileRef } from '../types'

interface EndUserProfileProps {
  onDataLoaded?: (data: { other_name?: string; id: string }) => void
}

const EndUserProfile = forwardRef<EndUserProfileRef, EndUserProfileProps>(({ onDataLoaded }, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const endUserProfileModalRef = useRef<EndUserProfileModalRef>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<EndUser | null>(null)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  // 记忆洞察
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
  const formatItems = useCallback(() => {
    return ['other_name', 'position', 'department', 'contact', 'phone', 'hire_date'].map(key => ({
      key,
      label: t(`userMemory.${key}`),
      children: key === 'hire_date' && data?.[key] ? dayjs(data[key as keyof EndUser]).format('YYYY-MM-DD') : String(data?.[key as keyof EndUser] || '-'),
    }))
  }, [data])
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
    >
      {loading
        ? <Skeleton />
        : <div className="rb:flex rb:flex-col rb:justify-between rb:gap-3 rb:h-full">
            {formatItems().map(vo => (
              <div key={vo.key} className="rb:flex rb:justify-between rb:items-center rb:gap-3 rb:leading-5">
                <div className="rb:text-[#5B6167]">{vo.label}</div>
                <div className="">{vo.children}</div>
              </div>
            ))}

          <div className="rb:border-t rb:border-t-[#DFE4ED] rb:pt-4 rb:text-[#5B6167] rb:text-[12px] rb:leading-4">
              {t('userMemory.updated_at')}: {data?.updatetime_profile ? dayjs(data?.updatetime_profile).format('YYYY/MM/DD HH:mm:ss') : ''}
            </div>
        </div>
      }
      <EndUserProfileModal
        ref={endUserProfileModalRef}
        refresh={getData}
      />
    </RbCard>
  )
})
export default EndUserProfile