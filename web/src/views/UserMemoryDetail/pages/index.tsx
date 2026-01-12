import { type FC, useEffect, useState, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Dropdown } from 'antd'

import PageHeader from '../components/PageHeader'
import StatementDetail from './StatementDetail'
import ForgetDetail from './ForgetDetail'
import ImplicitDetail from './ImplicitDetail'
import ShortTermDetail from './ShortTermDetail'
import PerceptualDetail from './PerceptualDetail'
import EpisodicDetail from './EpisodicDetail'
import ExplicitDetail from './ExplicitDetail'
import {
  getEndUserProfile,
} from '@/api/memory'

const Detail: FC = () => {
  const { t } = useTranslation()
  const { id, type } = useParams()
  const navigate = useNavigate()
  const [name, setName] = useState<string>('')
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
    return ['PERCEPTUAL_MEMORY', 'WORKING_MEMORY', 'EMOTIONAL_MEMORY', 'SHORT_TERM_MEMORY', 'IMPLICIT_MEMORY', 'EPISODIC_MEMORY', 'EXPLICIT_MEMORY', 'FORGETTING_MANAGEMENT']
      .map(key => ({ key, label: t(`userMemory.${key}`) }))
  }, [t])
  const onClick = ({ key }: { key: string }) => {
    navigate(`/user-memory/detail/${id}/${key}`, { replace: true })
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
      />
      <div className="rb:h-[calc(100vh-64px)] rb:overflow-y-auto rb:py-3 rb:px-4">
        {type === 'EMOTIONAL_MEMORY' && <StatementDetail />}
        {type === 'FORGETTING_MANAGEMENT' && <ForgetDetail />}
        {type === 'IMPLICIT_MEMORY' && <ImplicitDetail />}
        {type === 'SHORT_TERM_MEMORY' && <ShortTermDetail />}
        {type === 'PERCEPTUAL_MEMORY' && <PerceptualDetail />} {/** TODO */}
        {type === 'EPISODIC_MEMORY' && <EpisodicDetail />}
        {/* {type === 'WORKING_MEMORY' && <WorkingDetail />} */} {/** TODO */}
        {type === 'EXPLICIT_MEMORY' && <ExplicitDetail />} {/** TODO */}
      </div>
    </div>
  )
}

export default Detail