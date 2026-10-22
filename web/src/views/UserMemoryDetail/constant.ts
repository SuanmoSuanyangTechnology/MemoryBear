import type { EndUser } from './types'
import type { TagProps } from '@/components/Tag'

/** Maps episodic type keys to the custom Tag color presets. */
export const TAG_COLORS: Record<string, TagProps['color']> = {
  conversation: "processing",
  dialogue: 'processing',
  project_work: "success",
  learning: "warning",
  decision: "warning",
  important_event: "error",
  default: 'default'
}

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


const EmotionColor: Record<string, string> = {
  joy: '#FF8A4C',
  anger: '#FF5D34', 
  sadness: '#155EEF',
  fear: '#9C6FFF',
  neutral: '#4DA8FF',
  surprise: '#369F21',
  relief: '#36B5A0',
  anxiety: '#D99026',
  frustration: '#C75B7A',
  loneliness: '#667085',
  confusion: '#8B6BB1',
  hope: '#65A30D'
}
export const getEmotionColor = (emotionType: string) => {
  return EmotionColor[emotionType] || '#8c8c8c'
}