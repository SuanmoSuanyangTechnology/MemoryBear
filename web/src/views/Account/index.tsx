import React from 'react'
import { Account } from '@redbear/memory-brick'

import NotFound from '@/views/NotFound'
import { request } from '@/utils/request'

const isSaas = import.meta.env.VITE_PROD_ENV === 'saas'
const AccountPage: React.FC = () => {
  if (isSaas && Account) {
    return <Account request={request} />
  }
  return (
    <NotFound />
  )
};

export default AccountPage;