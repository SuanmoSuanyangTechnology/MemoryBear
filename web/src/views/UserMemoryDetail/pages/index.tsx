/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-07 20:37:34 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 11:47:31
 */
import { type FC, useState, useMemo, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Dropdown, Button, Flex, Space } from 'antd'

import PageHeader from '@/components/Layout/PageHeader'
import StatementDetail from './StatementDetail'
import ForgetDetail from './ForgetDetail'
import ImplicitDetail from './ImplicitDetail'
import ShortTermDetail from './ShortTermDetail'
import PerceptualDetail from './PerceptualDetail'
import EpisodicDetail from './EpisodicDetail'
import ExplicitDetail from './ExplicitDetail'
import WorkingDetail from './WorkingDetail'
import GraphDetail from './GraphDetail'

/**
 * Detail page for user memory - renders different memory type views
 * based on the `type` route param
 */
const Detail: FC = () => {
  const { t } = useTranslation()
  const { id, type } = useParams()
  const navigate = useNavigate()
  // Refs for child components that support imperative refresh
  const forgetDetailRef = useRef<{ handleRefresh: () => void }>(null)
  const statementDetailRef = useRef<{ handleRefresh: () => void }>(null)
  const implicitDetailRef = useRef<{ handleRefresh: () => void }>(null)

  // Build dropdown menu items for switching between memory types
  const items = useMemo(() => {
    return ['PERCEPTUAL_MEMORY', 'WORKING_MEMORY', 'EMOTIONAL_MEMORY', 'SHORT_TERM_MEMORY', 'IMPLICIT_MEMORY', 'EPISODIC_MEMORY', 'EXPLICIT_MEMORY', 'FORGET_MEMORY']
      .map(key => ({ key, label: t(`userMemory.${key}`) }))
  }, [t])

  // Navigate to the selected memory type detail page
  const onClick = ({ key }: { key: string }) => {
    navigate(`/user-memory/detail/${id}/${key}`, { replace: true })
  }

  const [loading, setLoading] = useState(false)

  // Trigger refresh on the active memory type's child component
  const handleRefresh = () => {
    setLoading(true)
    let response: any = null
    switch(type) {
      case 'FORGET_MEMORY':
        forgetDetailRef.current?.handleRefresh()
        break;
      case 'EMOTIONAL_MEMORY':
        response = statementDetailRef.current?.handleRefresh()
        break
      case 'IMPLICIT_MEMORY':
        response = implicitDetailRef.current?.handleRefresh()
        break
    }

    // If the child returns a Promise, wait for it before clearing loading state
    if (response instanceof Promise) {
      response.finally(() => {
        setLoading(false)
      })
    } else {
      setLoading(false)
    }
  }

  if (type === 'GRAPH') {
    return <GraphDetail />
  }
  const handleGoBack = () => {
    navigate(`/user-memory/neo4j/${id}`, { replace: true })
  }

  return (
    <div className="rb:h-full rb:w-full">
      <PageHeader
        title={
          <Dropdown menu={{ items, onClick, selectedKeys: type ? [type] : [] }}>
            <Flex align="center" gap={8} className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-6 rb:cursor-pointer rb:group">
              {type ? t(`userMemory.${type}`) : ''}
              <div
                className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/userMemory/up_border.svg')] rb:group-hover:transform-[rotate(180deg)]"
              ></div>
            </Flex>
          </Dropdown>
        }
        extra={
          <Space size={12}>
            {['FORGET_MEMORY', 'EMOTIONAL_MEMORY', 'IMPLICIT_MEMORY'].includes(type as string) &&
              <Button
                className="rb:px-2! rb:gap-0.5!"
                loading={loading}
                icon={<div className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/refresh_dark.svg')]"></div>}
                onClick={handleRefresh}
              >
                {t('common.refresh')}
              </Button>
            }
            <Button
              className="rb:px-2! rb:gap-0.5!"
              icon={<div className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/return.svg')]"></div>}
              onClick={handleGoBack}
            >
              {t('common.return')}
            </Button>
          </Space>
        }
      />
      <div className="rb:h-[calc(100vh-64px)] rb:overflow-y-auto rb:p-3">
        {type === 'EMOTIONAL_MEMORY' && <StatementDetail ref={statementDetailRef} refresh={handleRefresh} />}
        {type === 'FORGET_MEMORY' && <ForgetDetail ref={forgetDetailRef} />}
        {type === 'IMPLICIT_MEMORY' && <ImplicitDetail ref={implicitDetailRef} refresh={handleRefresh} />}
        {type === 'SHORT_TERM_MEMORY' && <ShortTermDetail />}
        {type === 'PERCEPTUAL_MEMORY' && <PerceptualDetail />}
        {type === 'EPISODIC_MEMORY' && <EpisodicDetail />}
        {type === 'WORKING_MEMORY' && <WorkingDetail />}
        {type === 'EXPLICIT_MEMORY' && <ExplicitDetail />}
      </div>
    </div>
  )
}

export default Detail