import { type FC, useEffect, useRef, useState } from 'react'
import { Form } from 'antd'
import { useTranslation } from 'react-i18next'

import { getApplication, getReleaseList } from '@/api/application'
import Knowledge from '@/components/Knowledge'
import type { Application } from '@/views/ApplicationManagement/types'
import type { Config as AgentConfig } from '@/views/ApplicationConfig/types/config'
import type { Release } from '@/views/ApplicationConfig/types/release'
import type { Variable } from '@/views/ApplicationConfig/components/VariableList/types'
import BasicField from '../BasicField'
import ErrorHandle from '../ErrorHandle'
import MemoryConfig from '../MemoryConfig'
import MessageEditor from '../MessageEditor'
import ModelConfig from '../ModelConfig'
import { useProperties } from '../PropertiesContext'
import ToolList from '../ToolList'
import AgentReferenceConfig from './AgentReferenceConfig'
import AgentVariableMapping from './AgentVariableMapping'

interface ReferencedAgentConfig {
  appId: string
  variables: Variable[]
  fileUploadEnabled: boolean
}

/** Renders all configuration fields owned by an Agent node. */
const Agent: FC = () => {
  const { t } = useTranslation()
  const {
    configs,
    values,
    data,
    form,
    selectedNode,
    graphRef,
    appType,
    getFilteredVariableList,
  } = useProperties()
  const [referencedAgentConfig, setReferencedAgentConfig] = useState<ReferencedAgentConfig>()
  const [referencedReleases, setReferencedReleases] = useState<Release[]>([])
  const [referencedReleasesLoading, setReferencedReleasesLoading] = useState(false)
  const [currentReleaseId, setCurrentReleaseId] = useState<string | undefined>()
  const referencedAgentRequestRef = useRef(0)
  const loadedAppIdRef = useRef<string | undefined>()
  const releasesLoadedRef = useRef(false)
  const agentMode = (values as any)?.mode
  const reference = (values as any)?.reference as
    | { app_id?: string; release_policy?: 'current' | 'pinned'; release_id?: string }
    | undefined
  const referencedAppId = reference?.app_id
  const referencedReleasePolicy = reference?.release_policy ?? 'current'
  const referencedReleaseId = reference?.release_id

  useEffect(() => {
    if (data.type !== 'agent' || agentMode !== 'reference' || !referencedAppId) {
      loadedAppIdRef.current = undefined
      releasesLoadedRef.current = false
      setReferencedAgentConfig(undefined)
      setReferencedReleases([])
      setReferencedReleasesLoading(false)
      setCurrentReleaseId(undefined)
      return
    }

    if (loadedAppIdRef.current === referencedAppId) return
    loadedAppIdRef.current = referencedAppId
    releasesLoadedRef.current = false

    const requestId = ++referencedAgentRequestRef.current
    setReferencedReleasesLoading(true)
    setReferencedReleases([])
    getReleaseList(referencedAppId)
      .then(response => {
        if (requestId !== referencedAgentRequestRef.current) return
        const list = Array.isArray(response)
          ? response
          : (response as { items?: Release[] } | undefined)?.items ?? []
        setReferencedReleases(list as Release[])
        releasesLoadedRef.current = true
      })
      .catch(() => {
        if (requestId === referencedAgentRequestRef.current) {
          setReferencedReleases([])
        }
      })
      .finally(() => {
        if (requestId === referencedAgentRequestRef.current) {
          setReferencedReleasesLoading(false)
        }
      })

    getApplication(referencedAppId)
      .then(app => {
        if (requestId !== referencedAgentRequestRef.current) return
        setCurrentReleaseId((app as Application)?.current_release_id)
      })
      .catch(() => {
        if (requestId === referencedAgentRequestRef.current) {
          setCurrentReleaseId(undefined)
        }
      })

    return () => {
      if (requestId === referencedAgentRequestRef.current) {
        referencedAgentRequestRef.current += 1
      }
    }
  }, [agentMode, data.type, referencedAppId])

  useEffect(() => {
    if (data.type !== 'agent' || agentMode !== 'reference' || !referencedAppId) {
      setReferencedAgentConfig(undefined)
      return
    }

    setReferencedAgentConfig(undefined)

    const applyConfig = (agentConfig: AgentConfig) => {
      const rawVariables = Array.isArray(agentConfig.variables) ? agentConfig.variables : []
      const variables = [...new Map(
        rawVariables
          .filter(variable => Boolean(variable.name))
          .map(variable => [variable.name, variable])
      ).values()]
      const fileUploadEnabled = agentConfig.features?.file_upload?.enabled === true
      setReferencedAgentConfig({
        appId: referencedAppId,
        variables,
        fileUploadEnabled,
      })

      const variableNames = new Set(variables.map(variable => variable.name))
      const currentMappings = form.getFieldValue('variable_mapping') as Array<{ name?: string; value?: string }> | undefined
      if (Array.isArray(currentMappings)) {
        const usedNames = new Set<string>()
        const validMappings = currentMappings.filter(mapping => {
          if (!mapping?.name || !variableNames.has(mapping.name) || usedNames.has(mapping.name)) return false
          usedNames.add(mapping.name)
          return true
        })
        if (validMappings.length !== currentMappings.length) {
          form.setFieldValue('variable_mapping', validMappings)
        }
      }

      if (!fileUploadEnabled) {
        form.setFieldValue('files', undefined)
      }
    }

    const applyError = () => {
      setReferencedAgentConfig(undefined)
    }

    if (referencedReleasePolicy === 'pinned') {
      const pinnedReleaseId = form.getFieldValue(['reference', 'release_id']) as string | undefined
      if (pinnedReleaseId && releasesLoadedRef.current && !referencedReleases.some(item => item.id === pinnedReleaseId)) {
        form.setFieldValue(['reference', 'release_id'], undefined)
      }

      const release = referencedReleases.find(item => item.id === referencedReleaseId)
      if (!release?.config) {
        applyError()
        return
      }
      applyConfig(release.config)
    } else {
      const release = referencedReleases.find(item => item.id === currentReleaseId)
      if (!release?.config) {
        applyError()
        return
      }
      applyConfig(release.config)
    }
  }, [agentMode, currentReleaseId, data.id, data.type, form, referencedAppId, referencedReleasePolicy, referencedReleaseId, referencedReleases])

  return (
    <>
      {Object.keys(configs).map(key => {
        const config = configs[key] || {}
        if ((config.dependsOn && (values as any)?.[config.dependsOn as string] !== config.dependsOnValue)
          || (key === 'files' && !referencedAgentConfig?.fileUploadEnabled)
        ) {
          return null
        }

        if (config.type === 'agentReference') {
          return (
            <AgentReferenceConfig
              key={key}
              releaseList={referencedReleases}
              releasesLoading={referencedReleasesLoading}
            />
          )
        }

        if (config.type === 'agentVariableMapping') {
          return (
            <AgentVariableMapping
              key={key}
              agentVariables={referencedAgentConfig?.variables ?? []}
              options={getFilteredVariableList(selectedNode.data.type, key).filter(variable => variable.dataType !== 'secret')}
            />
          )
        }

        if (key === 'model') {
          return (
            <ModelConfig
              key={key}
              parentName={key}
              variableOptions={getFilteredVariableList(selectedNode.data.type)}
              hideStructuredOutputConfig
            />
          )
        }

        if (config.type === 'toolList') {
          return (
            <Form.Item key={key} name={key}>
              <ToolList />
            </Form.Item>
          )
        }

        if (config.type === 'messageEditor') {
          return (
            <Form.Item key={key} name={key} required={config.required}>
              <MessageEditor
                title={t(`workflow.config.${selectedNode.data.type}.${key}`)}
                placeholder={t(config.placeholder || 'common.pleaseEnter')}
                isArray={!!config.isArray}
                parentName={key}
                options={getFilteredVariableList(selectedNode.data.type, key)}
                titleVariant={config.titleVariant}
                size="small"
              />
            </Form.Item>
          )
        }

        if (config.type === 'memoryConfig') {
          if (appType === 'pure_workflow') return null
          return (
            <Form.Item key={key} name={key} noStyle>
              <MemoryConfig
                parentName={key}
                needMsg={config.needMsg as boolean}
                options={getFilteredVariableList('llm')}
              />
            </Form.Item>
          )
        }

        if (config.type === 'knowledge') {
          return (
            <Form.Item key={key} name={key}>
              <Knowledge variant="workflow" required={config.required} />
            </Form.Item>
          )
        }

        if (config.type === 'errorHandle') {
          return (
            <Form.Item key={key} name={key}>
              <ErrorHandle selectedNode={selectedNode} graphRef={graphRef} />
            </Form.Item>
          )
        }

        return <BasicField key={key} configKey={key} config={config} />
      })}
    </>
  )
}

export default Agent
