import { create } from 'zustand'
import { getTenantSubscription } from '@/api/user'
import type { Subscription } from '@/components/SiderMenu'

interface SubscriptionState {
  subscription: Subscription | null
  fetchSubscription: () => Promise<Subscription | null>
}

export const useSubscription = create<SubscriptionState>((set) => ({
  subscription: null,
  fetchSubscription: () =>
    getTenantSubscription().then((res) => {
      const data = res as Subscription
      set({ subscription: data })
      return data
    }),
}))
