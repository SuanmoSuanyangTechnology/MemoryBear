import type { FC } from 'react'
import { Flex } from 'antd'
import { useTranslation } from 'react-i18next'

import RbAlert from '@/components/RbAlert'
import ScoreBreakdown from './ScoreBreakdown'
import type { LogItem } from '../types'

type RecordValue = Record<string, unknown>

const asRecord = (value: unknown): RecordValue => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as RecordValue
    : {}
)

const firstText = (source: RecordValue, keys: string[]) => {
  for (const key of keys) {
    const value = source[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return undefined
}

const UserMetadataContent: FC<{ log: LogItem }> = ({ log }) => {
  const { t } = useTranslation()
  const data = asRecord(log.data)
  const profile = asRecord(data.profile ?? data.metadata ?? data.user_metadata)
  const profileData = asRecord(profile.data)
  const source = Object.keys(profileData).length ? profileData : profile
  const aliases = Array.isArray(source.aliases_name) ? source.aliases_name : []
  const name = String(aliases[0] ?? source.name ?? t('memoryConversation.userMetadata.unknownUser'))
  const nodeId = firstText(data, ['node_id', 'id']) ?? `meta_user_${name}`
  const keyFileds = [
    'aliases_name',
    'core_facts',
    'interests',
    'events',
    'anchors',
    'beliefs_or_stances',
    'goals',
    'relations',
    'traits',
  ]
  const matchedKeyFileds = keyFileds.filter(key => (
    Object.prototype.hasOwnProperty.call(source, key)
  ))
  const fields = matchedKeyFileds.slice(0, 4)
  const metadataText = matchedKeyFileds
    .flatMap(key => {
      const value = source[key]
      if (Array.isArray(value)) return value
      return value === undefined || value === null || value === '' ? [] : [value]
    })
    .map(String)
    .join(', ')

  const description = firstText(source, ['description', 'description_summary'])
    ?? t('memoryConversation.userMetadata.profileDescription', {
      name: metadataText || name,
      fields: fields.join(','),
    })

  return (
    <Flex vertical gap={10}>
      <RbAlert color="orange" className="rb:block! rb:w-full! rb:text-[#171719]! rb:p-3!">
        <div className="rb:w-full">
          <Flex align="center" justify="space-between" gap={8} className="rb:mb-2!">
            <Flex align="center" gap={6} className="rb:min-w-0">
              <span className="rb:shrink-0 rb:rounded rb:bg-[#FFF0D8] rb:px-1.5 rb:py-0.5 rb:text-[10px] rb:font-medium rb:text-[#A77717]">
                {t('memoryConversation.userMetadata.metadataBadge')}
              </span>
              <code className="rb:overflow-hidden rb:text-[10px] rb:text-[#697481] rb:text-ellipsis rb:whitespace-nowrap">
                {nodeId}
              </code>
            </Flex>
            <span className="rb:shrink-0 rb:text-[10px] rb:text-[#A77717]">
              {t('memoryConversation.userMetadata.notRanked')}
            </span>
          </Flex>
          <p className="rb:text-[11px] rb:leading-5 rb:text-[#5B6167]">
            {description}
          </p>
          <ScoreBreakdown
            data={{
              raw_result_score: 1,
              node_type: 'ExtractedEntity',
              is_metadata: true,
            }}
          />
          <p className="rb:border-t rb:border-dashed rb:border-[#E7D8C6] rb:pt-2 rb:text-[10px] rb:leading-4 rb:text-[#697481]">
            {t('memoryConversation.scoreMerge.source', {
              id: nodeId,
              source: 'ExtractedEntity',
              query: '""',
            })}
          </p>
        </div>
      </RbAlert>
    </Flex>
  )
}

export default UserMetadataContent
