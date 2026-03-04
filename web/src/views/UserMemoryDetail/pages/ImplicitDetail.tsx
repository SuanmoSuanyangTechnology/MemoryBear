/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-08 19:46:02 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-04 16:26:55
 */
import { forwardRef, useImperativeHandle, useRef, useEffect } from 'react'
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
    implicitCheckData(id)
      .then(res => {
        if (!(res as { exists: boolean }).exists) {
            modal.confirm({
              title: t('implicitDetail.noData'),
              okText: t('common.refresh'),
              cancelText: t('common.cancel'),
              onOk: () => {
                refresh()
              }
            })
        }
      })
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

  return (
    <div className="rb:h-full rb:max-w-266 rb:mx-auto">
      <div className="rb:text-[#5B6167] rb:leading-5 rb:mt-3">{t('implicitDetail.title')}</div>
      
      <Preferences ref={preferencesRef} />

      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-3 rb:py-2.5 rb:font-medium rb:leading-5 rb:mb-4 rb:mt-6 rb:rounded-md">{t('implicitDetail.portraitTitle')}</div>
      <div className="rb:my-3 rb:text-[#5B6167] rb:leading-5">{t('implicitDetail.portraitSubTitle')}</div>
      <Row gutter={[16, 16]} className="rb:mt-4">
        <Col span={12}>
          <Portrait ref={portraitRef} />
        </Col>
        <Col span={12}>
          <InterestAreas ref={interestAreasRef} />
        </Col>
      </Row>

      <Habits ref={habitsRef} />
    </div>
  )
})
export default ImplicitDetail