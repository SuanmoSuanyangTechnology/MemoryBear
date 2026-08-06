import dayjs from 'dayjs';
import type { ActivityDateGroup } from './types'

const DEFAULT_TIME_ZONE = 'Asia/Shanghai'

export const getActivityDateGroup = (
  occurredAt: number,
  timeZone: string = DEFAULT_TIME_ZONE
): ActivityDateGroup => {
  const timestamp = occurredAt < 1_000_000_000_000 ? occurredAt * 1000 : occurredAt
  const occurredDate = dayjs(timestamp)
  if (!occurredDate.isValid()) return 'earlier'

  const zonedOccurredDate = occurredDate.tz(timeZone)
  const zonedToday = dayjs().tz(timeZone)

  if (zonedOccurredDate.isSame(zonedToday, 'day')) return 'today'
  if (zonedOccurredDate.isSame(zonedToday.subtract(1, 'day'), 'day')) return 'yesterday'
  return 'earlier'
}
