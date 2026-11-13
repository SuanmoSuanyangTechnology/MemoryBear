import { type FC } from 'react'
import { CommunityMarket } from '@redbear/memory-brick'

import { request } from '@/utils/request'
import PrivateWrap from '@/components/PrivateWrap'
import NotFound from '@/views/NotFound'

const CommunityMarketPage: FC = () => {
  return (
    <PrivateWrap fallback={<NotFound />}>
      {() => <CommunityMarket request={request} />}
    </PrivateWrap>
  )
}

export default CommunityMarketPage
