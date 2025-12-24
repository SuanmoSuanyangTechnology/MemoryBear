import { type FC, useEffect, useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Descriptions, Button } from 'antd';
import dayjs from 'dayjs'
import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty';
import {
  getEndUserProfile,
} from '@/api/memory'
import EndUserProfileModal from './EndUserProfileModal'
import type { EndUser, EndUserProfileModalRef } from '../types'

const EndUserProfile:FC = () => {
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
      setData(res as EndUser)
      setLoading(false) 
    })
    .finally(() => {
      setLoading(false)
    })
  }
  const formatItems = useCallback(() => {
    if (!data) return []
    return ['name', 'position', 'department', 'contact', 'phone', 'hire_date'].map(key => ({
      key,
      label: t(`userMemory.${key}`),
      children: key === 'hire_date' && data[key] ? dayjs(data[key as keyof EndUser]).format('YYYY-MM-DD') : String(data[key as keyof EndUser] || ''),
    }))
  }, [data])
  const handleEdit = () => {
    if (!data) return
    endUserProfileModalRef.current?.handleOpen(data)
  }
  return (
    <RbCard 
      title={t('userMemory.endUserProfile')} 
      headerType="borderless"
      headerClassName="rb:text-[18px]! rb:leading-[24px]"
    >
      {loading
        ? <Skeleton />
        : data
        ? <div className="rb:flex rb:flex-wrap rb:justify-between rb:h-full">
            <Descriptions column={1} items={formatItems()} classNames={{ label: 'rb:w-24' }} />
            <Button className="rb:mt-3" block onClick={handleEdit}>{t('common.edit')}</Button>
        </div>
        : <Empty size={80} />
      }
      <EndUserProfileModal
        ref={endUserProfileModalRef}
        refresh={getData}
      />
    </RbCard>
  )
}
export default EndUserProfile