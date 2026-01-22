import { type FC, useEffect, useState, useMemo, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Dropdown, Button } from 'antd'
import { LoadingOutlined } from '@ant-design/icons';

import PageHeader from '../components/PageHeader'
import StatementDetail from './StatementDetail'
import ForgetDetail from './ForgetDetail'
import ImplicitDetail from './ImplicitDetail'
import ShortTermDetail from './ShortTermDetail'
import PerceptualDetail from './PerceptualDetail'
import EpisodicDetail from './EpisodicDetail'
import ExplicitDetail from './ExplicitDetail'
import WorkingDetail from './WorkingDetail'
import {
  getEndUserProfile,
} from '@/api/memory'
import refreshIcon from '@/assets/images/refresh_hover.svg'
import GraphDetail from './GraphDetail'

const Detail: FC = () => {
  const { t } = useTranslation()
  const { id, type } = useParams()
  const navigate = useNavigate()
  const [name, setName] = useState<string>('')
  const forgetDetailRef = useRef<{ handleRefresh: () => void }>(null)
  const statementDetailRef = useRef<{ handleRefresh: () => void }>(null)
  const implicitDetailRef = useRef<{ handleRefresh: () => void }>(null)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  const getData = () => {
    if (!id) return
    getEndUserProfile(id).then((res) => {
      const response = res as { other_name: string; id: string; }
      setName(response.other_name || response.id) 
    })
  }
  const items = useMemo(() => {
    return ['PERCEPTUAL_MEMORY', 'WORKING_MEMORY', 'EMOTIONAL_MEMORY', 'SHORT_TERM_MEMORY', 'IMPLICIT_MEMORY', 'EPISODIC_MEMORY', 'EXPLICIT_MEMORY', 'FORGET_MEMORY']
      .map(key => ({ key, label: t(`userMemory.${key}`) }))
  }, [t])
  const onClick = ({ key }: { key: string }) => {
    navigate(`/user-memory/detail/${id}/${key}`, { replace: true })
  }

  const [loading, setLoading] = useState(false)
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

  return (
    <div className="rb:h-full rb:w-full">
      <PageHeader 
        name={name}
        source="node"
        operation={
          <Dropdown menu={{ items, onClick, selectedKeys: type ? [type] : [] }}>
            <div className="rb:cursor-pointer rb:group rb:flex rb:items-center rb:gap-1">
              - {type ? t(`userMemory.${type}`) : ''}
              <div
                className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/userMemory/up_border.svg')]  rb:transform-[rotate(180deg)] rb:group-hover:transform-[rotate(0deg)]"
              ></div>
            </div>
          </Dropdown>
        }
        extra={['FORGET_MEMORY', 'EMOTIONAL_MEMORY', 'IMPLICIT_MEMORY'].includes(type as string) &&
          <Button type="primary" ghost size="small" className="rb:h-6! rb:px-2! rb:leading-5.5!" loading={loading} onClick={handleRefresh}>
            {!loading && <img src={refreshIcon} className="rb:w-4 rb:h-4" /> }
            {t('common.refresh')}
          </Button>}
      />
      <div className="rb:h-[calc(100vh-64px)] rb:overflow-y-auto rb:py-3 rb:px-4">
        {type === 'EMOTIONAL_MEMORY' && <StatementDetail ref={statementDetailRef} />}
        {type === 'FORGET_MEMORY' && <ForgetDetail ref={forgetDetailRef} />}
        {type === 'IMPLICIT_MEMORY' && <ImplicitDetail ref={implicitDetailRef} />}
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