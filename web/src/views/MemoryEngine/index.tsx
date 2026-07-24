import { type FC } from 'react'
import { useParams } from 'react-router-dom'
import { DynamicWeightEngine, AssociationEngine } from '@redbear/memory-brick'

import { request } from '@/utils/request'
import PrivateWrap from '@/components/PrivateWrap'

const MemoryEngine: FC = () => {
  const { type } = useParams()

  if (type === 'dynamic-weight-engine') {
      return <PrivateWrap>{() => <DynamicWeightEngine request={request} />}</PrivateWrap>
  }
  if (type === 'association-engine') {
      return <PrivateWrap>{() => <AssociationEngine request={request} />}</PrivateWrap>
  }
    
  return null
}

export default MemoryEngine