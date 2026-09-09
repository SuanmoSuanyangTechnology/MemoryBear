import { type FC } from 'react'
import { BrainMemoryFlowHub } from '@redbear/memory-brick'

import { request } from '@/utils/request'
import PrivateWrap from '@/components/PrivateWrap'
import NotFound from '@/views/NotFound'

const BrainMemory: FC = () => {
  return <PrivateWrap fallback={<NotFound />}>{() => <BrainMemoryFlowHub request={request} />}</PrivateWrap>
}

export default BrainMemory