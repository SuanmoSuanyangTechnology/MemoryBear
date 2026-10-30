import type { FC } from 'react'
import { useTranslation } from 'react-i18next'
import type { CommunityD3Node, RawCommunityNode } from '@/components/D3Graph/types'

const CommunityEntityDetails: FC<{
  node: CommunityD3Node | RawCommunityNode
  variant?: 'hover' | 'selected'
}> = ({ node, variant = 'selected' }) => {
  const { t } = useTranslation()
  const isGroup = 'scenes' in node
  const nodeLabel = t(`userMemory.${node.label}`, { defaultValue: node.label })
  if (isGroup && variant === 'selected') {
    const title = node.properties.topic_name || node.properties.topic_scope || node.id
    return <div className="rb:wrap-break-word rb:text-sm rb:leading-5.5 rb:text-gray-900">
      <div className="rb:mb-3 rb:text-base rb:font-medium">{title}</div>
      <div className="rb:mb-2 rb:font-medium">{t('userMemory.summary')}</div>
      <div className="rb:mb-5 rb:rounded-xl rb:bg-gray-100 rb:px-3 rb:py-2.5 rb:whitespace-pre-wrap">{node.properties.summary || '—'}</div>
      <div className="rb:flex rb:items-center rb:justify-between rb:border-b rb:border-gray-200 rb:pb-3 rb:mb-3">
        <span className="rb:text-gray-600">{t('userMemory.sceneCount')}</span>
        <span>{node.scene_count}</span>
      </div>
      <div className="rb:mb-2 rb:font-medium">{t('userMemory.includedScenes')}</div>
      {node.scenes.length ? <ul className="rb:m-0 rb:list-disc rb:pl-4 rb:text-gray-600">
        {node.scenes.map(scene => <li key={scene.id}>
          {scene.properties.topic_name || scene.properties.topic_scope || scene.id}
        </li>)}
      </ul> : <div className="rb:text-gray-600">{t('userMemory.noScenes')}</div>}
    </div>
  }
  if (!isGroup && !node.isEmptyCommunity) {
    const summary = node.properties?.content || '—'
    const community = node.communityName 
    if (variant === 'hover') {
      return <div className="rb:wrap-break-word rb:text-[13px] rb:leading-5">
        <div className="rb:mb-1.5 rb:font-medium rb:text-gray-900">{node.name}</div>
        <div className="rb:mb-1.5 rb:whitespace-pre-wrap rb:text-gray-600 rb:rounded-xl rb:bg-gray-100 rb:px-3 rb:py-2.5">{summary}</div>
        <div><span className="rb:text-gray-600">{t('userMemory.type')}: </span><span className="rb:text-gray-900">{nodeLabel}</span></div>
        <div className="rb:mt-0.5"><span className="rb:text-gray-600">{t('userMemory.community')}: </span><span className="rb:text-gray-900">{community}</span></div>
      </div>
    }
    return <div className="rb:wrap-break-word rb:text-sm rb:leading-5.5 rb:text-gray-900">
      <div className="rb:mb-6 rb:text-base rb:font-medium">{node.name}</div>
      <div className="rb:mb-2 rb:font-medium">{t('userMemory.summary')}</div>
      <div className="rb:mb-5 rb:rounded-xl rb:bg-gray-100 rb:px-3 rb:py-2.5 rb:whitespace-pre-wrap">{summary}</div>
      <div className="rb:mb-2 rb:text-gray-600">{t('userMemory.type')}</div>
      <div>{nodeLabel}</div>
      {!node.unassignedReason && <>
        <div className="rb:mb-2 rb:mt-5 rb:text-gray-600">{t('userMemory.belongsToCommunity')}</div>
        <div>{community}</div>
      </>}
    </div>
  }
  return null
}
export default CommunityEntityDetails
