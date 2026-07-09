import { type FC } from 'react'
import { useParams } from 'react-router-dom'
import { DynamicWeightEngine, AssociationEngine } from '@redbear/memory-brick'

import { request } from '@/utils/request'

const MemoryEngine: FC = () => {
  const { type } = useParams()

  if (type === 'dynamic-weight-engine' && DynamicWeightEngine) {
      return <DynamicWeightEngine request={request} />
  }
  if (type === 'association-engine' && AssociationEngine) {
      return <AssociationEngine request={request} />
  }
    
  return null
}

export default MemoryEngine