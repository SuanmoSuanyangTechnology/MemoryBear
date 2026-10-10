import { useCallback, useMemo, useRef } from 'react'
import { App, Flex, Form } from 'antd'
import { useTranslation } from 'react-i18next'

import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import Knowledge from '@/components/Knowledge'
import type { AiPromptModalRef } from '../types'
import Tag from './Tag'
import AiPromptModal from './AiPromptModal'
import PromptConfigurationCard from './PromptConfigurationCard'
import SkillList from './Skill'
import VariableList from './VariableList/VariableList'
import type { Variable } from './VariableList/types'
import {
  buildVariablesFromNames,
  extractPromptVariables,
  findInvalidVariables,
} from '../hooks/agentHelpers'

const PROMPT_PATH: string[] = ['supervisor_config', 'system_prompt']
const KNOWLEDGE_PATH: string[] = ['supervisor_config', 'knowledge_retrieval']
const VARIABLES_PATH: string[] = ['supervisor_config', 'variables']
const SKILLS_PATH: string[] = ['supervisor_config', 'skills']

const withStableIndexes = (variables: Variable[]): Variable[] => {
  const createdAt = Date.now()
  return variables.map((variable, index) => ({
    ...variable,
    index: createdAt + index,
  }))
}

const SupervisorCapabilityConfig = () => {
  const { t } = useTranslation()
  const { modal } = App.useApp()
  const form = Form.useFormInstance()
  const aiPromptModalRef = useRef<AiPromptModalRef>(null)
  const defaultModelId = Form.useWatch('default_model_config_id', form) as string | undefined
  const watchedVariables = Form.useWatch(VARIABLES_PATH, form) as Variable[] | null | undefined
  const variables = useMemo(() => watchedVariables ?? [], [watchedVariables])

  const handlePrompt = () => {
    aiPromptModalRef.current?.handleOpen()
  }

  const updatePrompt = useCallback((value: string) => {
    if (!value) return

    form.setFieldValue(PROMPT_PATH, value)
    form.setFieldValue(VARIABLES_PATH, withStableIndexes(extractPromptVariables(value)))
  }, [form])

  const updateVariables = useCallback((value?: string) => {
    if (!value) return

    const invalidVariables = findInvalidVariables(value, variables.map(variable => variable.name))
    if (invalidVariables.length === 0) return

    modal.confirm({
      title: t('application.promptInvalidVariablesTitle'),
      content: (
        <Flex gap={8} wrap>
          {invalidVariables.map(variable => (
            <Tag key={variable} className="rb:break-all">
              {'{{'}{variable}{'}}'}
            </Tag>
          ))}
        </Flex>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: () => {
        const newVariables = withStableIndexes(buildVariablesFromNames(invalidVariables))
        form.setFieldValue(VARIABLES_PATH, [...variables, ...newVariables])
      },
    })
  }, [form, modal, t, variables])

  return (
    <>
      <PromptConfigurationCard
        title={t('application.supervisorCapabilityConfiguration')}
        promptLabel={t('application.supervisorPrompt')}
        promptDescription={t('application.supervisorPromptDesc')}
        fieldName={PROMPT_PATH}
        variableOptions={variables.map(variable => ({
          label: variable.display_name || variable.name,
          value: `{{${variable.name}}}`,
        }))}
        onPromptBlur={updateVariables}
        onAiPromptClick={handlePrompt}
        formItemClassName="rb:mb-6!"
      >
        <SwitchFormItem
          title={t('application.supervisorLongTermMemory')}
          name={['supervisor_config', 'memory', 'enabled']}
          desc={t('application.supervisorLongTermMemoryDesc')}
        />
      </PromptConfigurationCard>

      <Form.Item name={KNOWLEDGE_PATH} noStyle>
        <Knowledge variant="application" />
      </Form.Item>

      <Form.Item name={VARIABLES_PATH} noStyle>
        <VariableList name={VARIABLES_PATH} />
      </Form.Item>

      <Form.Item name={SKILLS_PATH} noStyle>
        <SkillList name={SKILLS_PATH} />
      </Form.Item>

      <AiPromptModal
        ref={aiPromptModalRef}
        defaultModelId={defaultModelId ?? null}
        refresh={updatePrompt}
      />
    </>
  )
}

export default SupervisorCapabilityConfig
