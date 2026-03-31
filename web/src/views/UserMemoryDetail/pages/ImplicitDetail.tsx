/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-08 19:46:02 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 11:18:50
 */
import { forwardRef, useImperativeHandle, useRef, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Row, Col, App } from 'antd'
import { useParams } from 'react-router-dom'

import Preferences from '../components/Preferences'
import Portrait from '../components/Portrait'
import InterestAreas from '../components/InterestAreas'
import Habits from '../components/Habits'
import {
  generateProfile,
  implicitCheckData,
} from '@/api/memory'
import RbCard from '@/components/RbCard/Card'
import RadioGroupButton from '@/components/RadioGroupButton'

/**
 * ImplicitDetail Component - Displays user's implicit memory profile
 * Shows unconscious preferences, personality traits, interests and habits
 */
const ImplicitDetail = forwardRef<{ handleRefresh: () => void; }, { refresh: () => void; }>(({
  refresh
}, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const { modal } = App.useApp()
  const preferencesRef = useRef<{ handleRefresh: () => void; }>(null)
  const portraitRef = useRef<{ handleRefresh: () => void; }>(null)
  const interestAreasRef = useRef<{ handleRefresh: () => void; }>(null)
  const habitsRef = useRef<{ handleRefresh: () => void; }>(null)
  
  // Check if implicit data exists, prompt user to initialize if not
  useEffect(() => {
    if (!id) return
    let modalInstance: { destroy: () => void } | null = null
    implicitCheckData(id)
      .then(res => {
        if (!(res as { exists: boolean }).exists) {
          modalInstance = modal.warning({
            title: t('implicitDetail.noData'),
            okText: t('common.refresh'),
            onOk: () => {
              refresh()
            }
          })
        }
      })
    return () => modalInstance?.destroy()
  }, [id])

  // Refresh all implicit memory components by regenerating profile
  const handleRefresh = () => {
    if (!id) {
      return Promise.resolve()
    }
    return new Promise((resolve, reject) => {
      generateProfile(id)
        .then(() => {
          preferencesRef.current?.handleRefresh()
          portraitRef.current?.handleRefresh()
          interestAreasRef.current?.handleRefresh()
          habitsRef.current?.handleRefresh()
          resolve(true)
        })
        .catch((error) => {
          reject(error)
        })
    })
  }
  useImperativeHandle(ref, () => ({
    handleRefresh
  }));

  const [activeTab, setActiveTab] = useState('preferences')
  const handleChangeTab = (value: unknown) => {
    setActiveTab(value as string)
  }

  return (
    <Row gutter={12} className="rb:h-full!">
      <Col span={12} className="rb:h-full!">
        <RbCard
          title={t('implicitDetail.subconscious')}
          headerType="borderless"
          headerClassName="rb:min-h-[50px]! rb:font-[MiSans-Bold] rb:font-bold"
          bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-54px)]"
          className="rb:h-full!"
        >
          <RadioGroupButton
            value={activeTab}
            options={[
              { value: 'preferences', label: t('implicitDetail.preferences') },
              { value: 'portrait', label: t('implicitDetail.portrait') },
            ]}
            onChange={handleChangeTab}
          />

          <div className="rb:mt-3 rb:h-[calc(100%-32px)]">
            {activeTab === 'preferences'
              ? <Preferences ref={preferencesRef} />
              : <div className="rb:h-full rb:overflow-y-auto">
                <Portrait ref={portraitRef} />
                <InterestAreas ref={interestAreasRef} />
              </div>
            }
          </div>
        </RbCard>
      </Col>
      <Col span={12} className="rb:h-full!">
        <Habits ref={habitsRef} />
      </Col>
    </Row>
  )
})
export default ImplicitDetail