import { type FC } from 'react'
import { useParams } from 'react-router-dom'
import {
    DynamicWeightEngine, AssociationEngine,
    ConsolidationEvolutionEngine, PredictionEngineSettings, PredictionEngine,
    PreferenceEngine
} from '@redbear/memory-brick'

import { request } from '@/utils/request'
import { handleSSE } from '@/utils/stream'
import PrivateWrap from '@/components/PrivateWrap'

const MemoryEngine: FC = () => {
  const { type } = useParams()

  if (type === 'dynamic-weight-engine') {
      return <PrivateWrap>{() => <DynamicWeightEngine request={request} />}</PrivateWrap>
  }
  if (type === 'association-engine') {
      return <PrivateWrap>{() => <AssociationEngine request={request} />}</PrivateWrap>
  }
  if (type === 'consolidation-evolution-engine') {
      return <PrivateWrap>{() => <ConsolidationEvolutionEngine request={request} />}</PrivateWrap>
  }
  if (type === 'prediction-engine') {
      return <PrivateWrap>{() => <PredictionEngineSettings request={request} />}</PrivateWrap>
  }
  if (type === 'prediction-progress') {
      return <PrivateWrap>{() => <PredictionEngine handleSSE={handleSSE} />}</PrivateWrap>
  }
  if (type === 'preference-engine') {
      return <PrivateWrap>{() => <PreferenceEngine request={request} />}</PrivateWrap>
  }
  return null
}

export default MemoryEngine