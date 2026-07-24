import React from 'react'
import { Account } from '@redbear/memory-brick'

import NotFound from '@/views/NotFound'
import { request } from '@/utils/request'
import { useSubscription } from '@/store/subscription'
import { useUser } from '@/store/user'
import PrivateWrap from '@/components/PrivateWrap'

const AccountPage: React.FC = () => {
  const { user } = useUser()
  const { subscription } = useSubscription()
  return (
    <PrivateWrap fallback={<NotFound />}>
      {() => <Account request={request} userInfo={user} plan={subscription} />}
    </PrivateWrap>
  )
};

export default AccountPage;