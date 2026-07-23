import { useEffect, useRef, useState, useImperativeHandle, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom';
import { Form, App, Flex } from 'antd'

import type {
  ModelConfigModalRef,
  ChatData,
  Config,
  ModelConfig,
  AgentRef,
  AiPromptModalRef,
  Source,
  ChatVariableConfigModalRef,
  FeaturesConfigForm
} from '../types'
import type { Variable } from '../components/VariableList/types'
import type { Model } from '@/views/ModelManagement/types'
import { getModelList } from '@/api/models';
import { saveAgentConfig, getApplicationConfig } from '@/api/application'
import { getMemoryConfigList } from '@/api/memory'
import type { Memory } from '@/views/MemoryManagement/types'
import { getListLogoUrl } from '@/views/ModelManagement/utils';
import Tag from '@/components/Tag'
import { buildAgentSaveParams, extractPromptVariables, buildVariablesFromNames, findInvalidVariables } from './agentHelpers'
import { useOpeningStatementSync } from './useOpeningStatementSync'

/**
 * Encapsulates all state, effects and handlers of the Agent configuration
 * screen. Keeping the logic here lets Agent.tsx stay a thin view layer.
 *
 * @param ref - Forwarded ref exposing the imperative AgentRef API
 * @param onFeaturesLoad - Callback fired when features config is (re)loaded
 */
export function useAgent(
  ref: React.ForwardedRef<AgentRef>,
  onFeaturesLoad?: (features: FeaturesConfigForm | undefined) => void
) {
  const { t } = useTranslation()
  const { id } = useParams();
  const { message, modal } = App.useApp()
  const [form] = Form.useForm()
  const [data, setData] = useState<Config | null>(null);
  const modelConfigModalRef = useRef<ModelConfigModalRef>(null)
  const [modelList, setModelList] = useState<Model[]>([])
  const [defaultModel, setDefaultModel] = useState<Model | null>(null)
  const [chatList, setChatList] = useState<ChatData[]>([])
  const values = Form.useWatch<Config>([], form)
  const [isSave, setIsSave] = useState(false)
  const initialized = useRef(false)

  // Initialization flag
  useEffect(() => {
    if (data) {
      initialized.current = true
    }
  }, [data])

  useEffect(() => {
    if (!initialized.current) return
    if (isSave) return
    setIsSave(true)
  }, [values])

  useEffect(() => {
    getModels()
    getData()
  }, [id])

  const [activeMemoryConfig, setActiveMemoryConfig] = useState<Memory | null>(null)
  const getActiveMemoryConfig = () => {
    getMemoryConfigList()
      .then((res) => {
        setActiveMemoryConfig((res as Memory[]).find(item => item.is_active) || null)
      })
      .catch(() => {
        setActiveMemoryConfig(null)
      })
  }
  useEffect(() => {
    getActiveMemoryConfig()
  }, [])


  /**
   * Fetch agent configuration data
   */
  const getData = () => {
    getApplicationConfig(id as string).then(res => {
      const response = res as Config
      const { skills, variables } = response
      const allSkills = Array.isArray(skills?.skill_ids) ? skills?.skill_ids.map(vo => ({ id: vo })) : []
      const allTools = Array.isArray(response.tools) ? response.tools : []
      const variableList = variables?.map((item, index) => ({
        ...item,
        index
      })) || []
      form.setFieldsValue({
        ...response,
        tools: allTools,
        memory: {
          ...response.memory,
        },
        skills: {
          ...skills,
          skill_ids: allSkills
        },
        variables: [...variableList]
      })
      updateVariableList([...variableList])
      setData({
        ...response,
        tools: allTools
      })
      onFeaturesLoad?.(response.features)
    })
  }

  /**
   * Refresh configuration after model changes
   * @param vo - Model configuration
   * @param type - Source type (model or chat)
   */
  const refresh = (vo: ModelConfig, type: Source) => {
    if (type === 'model') {
      const { default_model_config_id, capability, ...rest } = vo
      if (default_model_config_id !== values.default_model_config_id) {
        const fileUpload = { ...values.features?.file_upload }
        Object.keys(fileUpload).forEach(key => {
          if (key.includes('enabled')) {
            (fileUpload as Record<string, any>)[key] = false
          }
        })
        form.setFieldValue(['features', 'file_upload'], fileUpload)
        message.warning(t('application.resetFeaturesTip'))
      }
      form.setFieldsValue({
        default_model_config_id,
        capability,
        model_parameters: {...rest}
      })
      if (default_model_config_id === values?.default_model_config_id) {
        const label = defaultModel?.id === default_model_config_id && defaultModel?.name ? defaultModel.name : vo.label || ''
        setChatList([{
          label: label,
          model_config_id: default_model_config_id,
          model_parameters: {...rest},
          list: []
        }])
      }
    } else if (type === 'chat') {
      if (chatList.length >= 4) {
        message.warning(t('application.maxChatCount'))
        return
      }
      const { label, default_model_config_id, ...reset } = vo

      setChatList((prev: ChatData[]) => {
        const newChatItem: ChatData = {
          label,
          model_config_id: default_model_config_id,
          model_parameters: {...reset},
          list: []
        };
        if (prev.some(item => item.model_config_id === default_model_config_id)) return prev
        return [
          ...(prev || []).map(item => ({
            ...item,
            conversation_id: undefined,
            list: []
          })),
          newChatItem
        ];
      })
    }
  }

  /**
   * Open model configuration modal
   */
  const handleModelConfig = () => {
    modelConfigModalRef.current?.handleOpen('model', { ...defaultModel, model_parameters : values?.model_parameters })
  }
  /**
   * Clear all debugging chat sessions
   */
  const handleClearDebugging = () => {
    setChatList([])
  }

  /**
   * Save agent configuration
   * @param flag - Whether to show success message
   * @returns Promise that resolves when save is complete
   */
  const handleSave = (flag = true) => {
    if (!isSave || !data) return Promise.resolve()
    const params = buildAgentSaveParams(data, values)

    return new Promise((resolve, reject) => {
      saveAgentConfig(data.app_id, params)
      .then((res) => {
        if (flag) {
          message.success({ content: t('common.saveSuccess'), duration: 1 })
        }
        setIsSave(false)
        resolve(res)
      }).catch(error => {
        reject(error)
      })
    })
  }
  /**
   * Fetch available models list
   */
  const getModels = () => {
    getModelList({ type: 'llm,chat', pagesize: 100, page: 1, is_active: true })
      .then(res => {
        const response = res as { items: Model[] }
        setModelList(response.items)
      })
  }
  /**
   * Add new model for debugging
   */
  const handleAddModel = () => {
    modelConfigModalRef.current?.handleOpen('chat')
  }
  useEffect(() => {
    if (values?.default_model_config_id && modelList.length > 0) {
      const filterValue = modelList.find(item => item.id === values.default_model_config_id)
      setDefaultModel(filterValue as Model | null)
      setChatList([{
        label: filterValue?.name || '',
        model_config_id: filterValue?.id,
        model_parameters: {...(values?.model_parameters || {})} as unknown as ModelConfig,
        list: []
      }])
      form.setFieldValue('capability', filterValue?.capability)
    }
  }, [modelList, values?.default_model_config_id])

  useImperativeHandle(ref, () => ({
    handleSave,
    features: values?.features
  }))

  const aiPromptModalRef = useRef<AiPromptModalRef>(null)
  /**
   * Open AI prompt generation modal
   */
  const handlePrompt = () => {
    aiPromptModalRef.current?.handleOpen()
  }
  /**
   * Update prompt and extract variables
   * @param value - New prompt value
   */
  const updatePrompt = (value?: string) => {
    if (!value) return
    form.setFieldValue('system_prompt', value)
    updateVariableList(extractPromptVariables(value))
  }

  /**
   * Update variable list
   * @param list - New variable list
   */
  const updateVariableList = (list: Variable[]) => {
    form.setFieldValue('variables', [...list])
    setChatVariables([...list])
  }
  const chatVariableConfigModalRef = useRef<ChatVariableConfigModalRef>(null)
  const [chatVariables, setChatVariables] = useState<Variable[]>([])
  /**
   * Open chat variable configuration modal
   */
  const handleOpenVariableConfig = () => {
    chatVariableConfigModalRef.current?.handleOpen(chatVariables)
  }

  /**
   * Save chat variable configuration
   * @param values - Variable values
   */
  const handleSaveChatVariable = (variables: Variable[]) => {
    setChatVariables(variables)
  }
  useEffect(() => {
    setChatVariables(values?.variables || [])
  }, [values?.variables])

  const handleSaveFeaturesConfig = (value: FeaturesConfigForm) => {
    form.setFieldValue('features', value)
    const { statement = '' } = value?.opening_statement || {}
    onFeaturesLoad?.(value)

    if (value?.opening_statement?.enabled) {
      const variables = values?.variables
      const invalid = findInvalidVariables(statement, variables.map(v => v.name))
      if (invalid.length > 0) {
        const newVars = invalid.map((name, i) => ({
          index: variables.length + i,
          name,
          display_name: name,
          type: 'text',
          required: true,
          max_length: 48,
        }))

        form.setFieldValue('variables', [...variables, ...newVars])
      }
    }
  }
  const modelLogo = useMemo(() => {
    return defaultModel?.name && getListLogoUrl(defaultModel.provider, defaultModel.logo as string)
  }, [defaultModel])

  useOpeningStatementSync({
    form,
    defaultModel,
    chatVariables,
    chatListLength: chatList.length,
    setChatList,
  })

  const updateVariables = useCallback((value?: string) => {
    if (!value) return
    const invalid = findInvalidVariables(value, chatVariables.map((v: Variable) => v.name))

    if (invalid.length > 0) {
      modal.confirm({
        title: t('application.promptInvalidVariablesTitle'),
        content: <Flex gap={8} wrap>{invalid.map((vo, index) => <Tag key={index}>{'{{'}{vo}{'}}'}</Tag>)}</Flex>,
        okText: t('common.confirm'),
        cancelText: t('common.cancel'),
        onOk: () => {
          updateVariableList([...chatVariables, ...buildVariablesFromNames(invalid)])
        },
      })
    }
  }, [chatVariables])

  return {
    form,
    values,
    defaultModel,
    modelLogo,
    chatVariables,
    activeMemoryConfig,
    chatList,
    setChatList,
    modelConfigModalRef,
    aiPromptModalRef,
    chatVariableConfigModalRef,
    handleModelConfig,
    handleClearDebugging,
    handleSave,
    handleAddModel,
    handlePrompt,
    handleOpenVariableConfig,
    handleSaveChatVariable,
    handleSaveFeaturesConfig,
    updatePrompt,
    updateVariables,
    refresh,
  }
}
