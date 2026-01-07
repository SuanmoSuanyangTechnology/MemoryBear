import { type FC, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

import PageHeader from '../components/PageHeader'
import StatementDetail from './StatementDetail'
import ForgetDetail from './ForgetDetail'
import {
  getEndUserProfile,
} from '@/api/memory'

const Detail: FC = () => {
  const { id, type } = useParams()
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

  console.log('Detail', name)
  return (
    <div className="rb:h-full rb:w-full">
      <PageHeader 
        name={name}
        source="node"
      />
      <div className="rb:h-[calc(100vh-64px)] rb:overflow-y-auto rb:py-3 rb:px-4">
        {type === 'EMOTIONAL_MEMORY' && <StatementDetail />}
        {type === 'FORGETTING_MANAGEMENT' && <ForgetDetail />}
      </div>
    </div>
  )
}

export default Detail