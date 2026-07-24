import React from 'react'
import { Account } from '@redbear/memory-brick'

import NotFound from '@/views/NotFound'
import { request } from '@/utils/request'
import PrivateWrap from '@/components/PrivateWrap'
const AccountPage: React.FC = () => {
  return (
    <PrivateWrap fallback={<NotFound />}>
      {() => <Account request={request} />}
    </PrivateWrap>
  )
};

export default AccountPage;