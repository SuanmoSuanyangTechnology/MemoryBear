import { type FC, useEffect } from 'react'
import { Form, Select } from 'antd'
import { useTranslation } from 'react-i18next'

import { getApplicationListUrl } from '@/api/application'
import DebounceSelect from '@/components/DebounceSelect'
import type { AgentReference } from '../../../types'
import type { Release } from '@/views/ApplicationConfig/types/release'

interface AgentReferenceConfigProps {
  releaseList: Release[]
  releasesLoading: boolean
}

const AgentReferenceConfig: FC<AgentReferenceConfigProps> = ({
  releaseList,
  releasesLoading,
}) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance()
  const appId = Form.useWatch(['reference', 'app_id'], form) as string | undefined
  const releasePolicy = (Form.useWatch(
    ['reference', 'release_policy'],
    form
  ) as AgentReference['release_policy'] | undefined) ?? 'current'

  useEffect(() => {
    if (!form.getFieldValue(['reference', 'release_policy'])) {
      form.setFieldValue(['reference', 'release_policy'], 'current')
    }
  }, [form])

  const handleApplicationChange = (nextAppId?: string) => {
    if (nextAppId !== appId) {
      form.setFieldValue(['reference', 'release_id'], undefined)
      form.setFieldValue('variable_mapping', [])
      form.setFieldValue('files', undefined)
    }
  }

  const handlePolicyChange = (policy: AgentReference['release_policy']) => {
    if (policy !== releasePolicy) {
      form.setFieldValue(['reference', 'release_id'], undefined)
    }
  }

  return (
    <>
      <Form.Item
        name={['reference', 'app_id']}
        label={t('workflow.config.agent.referenceAgent')}
        required
      >
        <DebounceSelect
          url={getApplicationListUrl}
          params={{ status: 'active', type: 'agent', include_shared: false }}
          valueKey="id"
          labelKey="name"
          pageSize={100}
          labelInValue={false}
          placeholder={t('workflow.config.agent.referenceAgentPlaceholder')}
          className="rb:w-full"
          onChange={handleApplicationChange}
        />
      </Form.Item>

      <Form.Item
        name={['reference', 'release_policy']}
        label={t('workflow.config.agent.releasePolicy')}
        required
      >
        <Select
          options={[
            { label: t('workflow.config.agent.currentRelease'), value: 'current' },
            { label: t('workflow.config.agent.pinnedRelease'), value: 'pinned' },
          ]}
          className="rb:w-full"
          onChange={handlePolicyChange}
        />
      </Form.Item>

      {releasePolicy === 'pinned' && (
        <Form.Item
          name={['reference', 'release_id']}
          label={t('workflow.config.agent.releaseVersion')}
          required
        >
          <Select
            allowClear
            loading={releasesLoading}
            disabled={!appId}
            options={releaseList.map(item => ({
              label: item.version_name || item.name || (item.version ? `v${item.version}` : item.id),
              value: item.id,
            }))}
            placeholder={t('workflow.config.agent.releaseVersionPlaceholder')}
            className="rb:w-full"
          />
        </Form.Item>
      )}
    </>
  )
}

export default AgentReferenceConfig
