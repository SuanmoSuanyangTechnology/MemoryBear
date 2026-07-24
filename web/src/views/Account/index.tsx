import React from 'react'
import { Account } from '@redbear/memory-brick'

import NotFound from '@/views/NotFound'
import { request } from '@/utils/request'
import { useSubscription } from '@/store/subscription'
import { useUser } from '@/store/user'

const isSaas = import.meta.env.VITE_PROD_ENV === 'saas'
const AccountPage: React.FC = () => {
  const { user } = useUser()
  const { subscription } = useSubscription()

  if (isSaas && Account) {
    return <Account request={request} userInfo={user} plan={subscription} />
  }
  return (
    <NotFound />
  )
};

export default AccountPage;