/**
 * End User Profile Component
 * Displays and manages end user profile information
 */

import { forwardRef, useImperativeHandle, useEffect, useState, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, List, Select } from 'antd';
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import {
  getEndUserInfo,
} from '@/api/memory'
import EndUserProfileModal from './EndUserProfileModal'
import type { EndUser, EndUserProfileModalRef, EndUserProfileRef, SpecialScene } from '../types'
import { OLD_META_DATA_KEY_FIELDS, NEW_META_DATA_KEY_FIELDS } from '../constant';

/**
 * Component props
 */
interface EndUserProfileProps {
  onDataLoaded?: (data?: EndUser) => void;
  className?: string;
}

const formatValue = (value: string | string[] | null | undefined) => {
  if (!value) return '-'
  if (Array.isArray(value)) {
    return value.length ? value.join(', ') : '-'
  }
  return value
}
const EndUserProfile = forwardRef<EndUserProfileRef, EndUserProfileProps>(({ className, onDataLoaded }, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const endUserProfileModalRef = useRef<EndUserProfileModalRef>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<EndUser | null>(null)
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null)
  const [specialScenes, setSpecialScenes] = useState<SpecialScene[]>([])

  /** Fetch profile data */
  const getData = useCallback(() => {
    if (!id) return
    setSelectedSceneId(null)
    setSpecialScenes([])
    setLoading(true)
    getEndUserInfo(id).then((res) => {
      const userData = res as EndUser
      setData(userData)
      setLoading(false)
      const specialSceneList = Object.entries(userData?.meta_data?.domain_specific_info ?? {}).map(
        ([sceneId, fields]) => ({
          id: sceneId,
          name: sceneId,
          fields: Object.entries(fields).map(([fieldKey, field]) => ({
            key: `${sceneId}.${fieldKey}`,
            label: field.field_label || fieldKey,
            value: formatValue(field.values),
          })),
        }),
      )
      setSpecialScenes(specialSceneList)
      setSelectedSceneId(specialSceneList[0].id)
    })
    .finally(() => {
      setLoading(false)
    })
  }, [id])

  useEffect(() => {
    onDataLoaded?.(data as EndUser)
  }, [onDataLoaded, data])

  useEffect(() => {
    if (!id) return
    getData()
  }, [id, getData])
  /** Open edit modal */
  const handleEdit = () => {
    if (!data) return
    endUserProfileModalRef.current?.handleOpen(data)
  }

  useImperativeHandle(ref, () => ({
    data
  }));

  const hasOldMetaData = OLD_META_DATA_KEY_FIELDS.some(
    (key) => (data?.meta_data?.[key]?.length ?? 0) > 0,
  )
  const metaDataKeyFields = hasOldMetaData
    ? OLD_META_DATA_KEY_FIELDS
    : NEW_META_DATA_KEY_FIELDS
  const profileItems = [
    { key: 'other_name', label: t('userMemory.other_name'), value: data?.other_name || '-' },
    ...metaDataKeyFields
      .filter((key) => {
        const value = data?.meta_data?.[key]
        return value && (!Array.isArray(value) || value.length > 0)
      })
      .map((key) => ({
        key,
        label: t(`userMemory.meta_data.${key}`),
        value: formatValue(data?.meta_data?.[key]),
      })),
  ]
  const selectedScene = specialScenes.find((scene) => scene.id === selectedSceneId) ?? specialScenes[0]

  return (
    <RbCard 
      title={t('userMemory.endUserProfile')} 
      extra={
        <div 
          className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]" 
          onClick={handleEdit}
        ></div>
      }
      headerClassName="rb:min-h-[46px]! rb:font-medium!"
      className={clsx("rb:bg-[#FFFFFF]! rb:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.13)]! rb:absolute! rb:w-100 rb:top-3 rb:left-0 rb:bottom-0 rb:z-100", className)}
      bodyClassName="rb:px-5! rb:py-0! rb:max-h-[calc(100%-46px)]! rb:overflow-auto"
    >
      {loading
        ? <Skeleton />
        : <div className="rb:leading-5">
          <List
            dataSource={[
              ...profileItems,
              ...(specialScenes.length
                ? [{ key: 'special_scene', label: t('userMemory.specialScene'), value: (
                  <Select
                    className="rb:w-full"
                    value={selectedSceneId}
                    onChange={setSelectedSceneId}
                    fieldNames={{label: 'name', value: 'id'}}
                    options={specialScenes}
                  />
                )}]
                : []
              ),
              ...(selectedScene?.fields ?? []),
            ]}
            renderItem={(item) => (
              <List.Item key={item.key} className={item.key === 'special_scene' ? 'rb:border-0! rb:mt-3!' : undefined}>
                <div className="rb:w-24 rb:shrink-0 rb:mr-3 rb:text-[#7B8085]">{item.label}</div>
                <div className="rb:flex-1 rb:min-w-0 rb:wrap-break-word">{item.value}</div>
              </List.Item>
            )}
          />
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