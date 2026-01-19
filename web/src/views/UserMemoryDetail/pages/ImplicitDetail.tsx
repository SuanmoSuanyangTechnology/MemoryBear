import { forwardRef, useImperativeHandle, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Row, Col } from 'antd'
import { useParams } from 'react-router-dom'

import Preferences from '../components/Preferences'
import Portrait from '../components/Portrait'
import InterestAreas from '../components/InterestAreas'
import Habits from '../components/Habits'
import {
  generateProfile,
} from '@/api/memory'

const ImplicitDetail = forwardRef<{ handleRefresh: () => void; }>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const preferencesRef = useRef<{ handleRefresh: () => void; }>(null)
  const portraitRef = useRef<{ handleRefresh: () => void; }>(null)
  const interestAreasRef = useRef<{ handleRefresh: () => void; }>(null)
  const habitsRef = useRef<{ handleRefresh: () => void; }>(null)

  const handleRefresh = () => {
    if (!id) return
    generateProfile(id)
      .then(() => {
        preferencesRef.current?.handleRefresh()
        portraitRef.current?.handleRefresh()
        interestAreasRef.current?.handleRefresh()
        habitsRef.current?.handleRefresh()
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