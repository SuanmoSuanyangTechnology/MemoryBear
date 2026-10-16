import type { EndUser } from './types'

export const SIDEBAR_MENU_ITEMS = [
  {
    key: 'userProfile',
    labelKey: 'userMemory.userProfile',
    iconClassName: "rb:bg-[url('@/assets/images/userMemory/userProfile.svg')]",
    activeIconClassName: "rb:bg-[url('@/assets/images/userMemory/userProfile_active.svg')]",
  },
  {
    key: 'aboutMe',
    labelKey: 'userMemory.aboutMe',
    iconClassName: "rb:bg-[url('@/assets/images/userMemory/aboutMe.svg')]",
    activeIconClassName: "rb:bg-[url('@/assets/images/userMemory/aboutMe_active.svg')]",
  },
  {
    key: 'interestDistribution',
    labelKey: 'userMemory.interestDistribution',
    iconClassName: "rb:bg-[url('@/assets/images/userMemory/interestDistribution.svg')]",
    activeIconClassName: "rb:bg-[url('@/assets/images/userMemory/interestDistribution_active.svg')]",
  },
  {
    key: 'memoryInsight',
    labelKey: 'userMemory.memoryInsight',
    iconClassName: "rb:bg-[url('@/assets/images/userMemory/memoryInsight.svg')]",
    activeIconClassName: "rb:bg-[url('@/assets/images/userMemory/memoryInsight_active.svg')]",
  },
]

export const OLD_META_DATA_KEY_FIELDS: (keyof EndUser['meta_data'])[] = ['alias', 'relations', 'goals', 'core_facts', 'interests', 'traits', 'beliefs_or_stances', 'anchors']
export const NEW_META_DATA_KEY_FIELDS: (keyof EndUser['meta_data'])[] = ['age', 'from', 'residence', 'education', 'occupation', 'family', 'religion_or_politics', 'health', 'person', 'group', 'object', 'pet']
