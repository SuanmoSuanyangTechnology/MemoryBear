import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Skeleton } from 'antd'
import { useTranslation } from 'react-i18next'

import { getEndUserInfo } from '@/api/memory'
import RbCard from '@/components/RbCard/Card'
import PermanentMemoryNode from './PermanentMemoryNode'
import usePermanentMemoryDrop from './usePermanentMemoryDrop'
import type { EndUser } from '../types'
import PermanentMemoryDetailModal, { type PermanentMemoryDetailModalRef, type PermanentMemoryFact } from './PermanentMemoryDetailModal'

interface PermanentMemoryPanelProps {
  userId: string;
  onVisibilityChange?: (visible: boolean) => void;
}

/** Displays permanent profile facts and reloads them on demand. */
export default function PermanentMemoryPanel({ userId, onVisibilityChange }: PermanentMemoryPanelProps) {
  const { t } = useTranslation()
  const detailRef = useRef<PermanentMemoryDetailModalRef>(null)
  const [facts, setFacts] = useState<PermanentMemoryFact[]>([])
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision(value => value + 1), [])

  useEffect(() => {
    let active = true
    if (!userId) {
      setFacts([])
      setLoading(false)
      return
    }
    setLoading(true)
    getEndUserInfo(userId)
      .then(response => {
        if (!active) return
        const metadata = (response as EndUser).meta_data
        const values = Object.entries(metadata || {}).flatMap(([key, value]) => {
          if (key === 'domain_specific_info') return []
          return Array.isArray(value) ? value.filter((fact): fact is string => typeof fact === 'string' && Boolean(fact.trim())).join(', ') : []
        })
        const generalFacts = [...new Set(values)].map((content, index) => ({
          id: `general-${index}`,
          title: content,
          content,
        }))
        let fieldColorIndex = 0
        const sceneFacts = Object.entries(metadata?.domain_specific_info || {}).flatMap(([scene, fields]) => {
          const colorIndex = fieldColorIndex++
          return Object.entries(fields).flatMap(([key, field]) => {
            const content = (field.values || []).join(', ')
            return {
              id: JSON.stringify([scene, key]),
              title: content,
              content,
              scene,
              colorIndex,
            }
          })
        })
        setFacts([...generalFacts, ...sceneFacts])
      })
      .catch(() => {
        if (active) setFacts([])
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [userId, revision])

  const visible = facts.length > 0
  useEffect(() => {
    onVisibilityChange?.(visible)
  }, [visible, onVisibilityChange])

  const contentKey = JSON.stringify([revision, facts.map(fact => [fact.id, fact.title])])
  const { viewportRef, stageRef } = usePermanentMemoryDrop(contentKey, loading)

  if (!visible) return null

  return (
    <RbCard
      title={<>
        <span className="rb:font-[MiSans-Bold] rb:font-bold">{t('explicitDetail.permanentMemory')}</span>
        <span className="rb:font-normal! rb:ml-1 rb:text-gray-600 rb:text-[13px]">{facts.length}</span>
      </>}
      extra={<Button type="text" loading={loading} onClick={refresh}>{t('explicitDetail.refresh')}</Button>}
      headerType="borderless"
      headerClassName="rb:min-h-[53px]!"
      bodyClassName="rb:px-4! rb:py-0!"
      className="rb:w-full!"
    >
      {loading
        ? <Skeleton active />
        : facts.length
        ? (
          <div ref={viewportRef} className="rb:overflow-x-hidden rb:overflow-y-auto rb:[overflow-anchor:none] rb:scrollbar-gutter-stable">
            <div
              ref={stageRef}
              className="rb:relative rb:w-full"
            >
              {facts.map(fact => (
                <PermanentMemoryNode
                  key={fact.id}
                  fact={fact}
                  onOpen={value => detailRef.current?.handleOpen(value)}
                />
              ))}
            </div>
          </div>
        )
        : null
      }
      <PermanentMemoryDetailModal key={userId} ref={detailRef} />
    </RbCard>
  )
}
