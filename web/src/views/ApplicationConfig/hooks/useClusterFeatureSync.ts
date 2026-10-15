import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import type { FormInstance } from 'antd'

import type { ChatData, Config, FeaturesConfigForm } from '../types'
import type { Variable } from '../components/VariableList/types'
import { appendMissingOpeningStatementVariables } from './agentHelpers'
import { useOpeningStatementSync } from './useOpeningStatementSync'

interface UseClusterFeatureSyncParams {
  form: FormInstance<Config>
  values?: Config
  chatListLength: number
  setChatList: Dispatch<SetStateAction<ChatData[]>>
  onFeaturesLoad?: (features: FeaturesConfigForm | undefined) => void
}

/**
 * Synchronize supervisor feature settings, runtime variables, and the Cluster
 * debugging opening statement.
 */
export function useClusterFeatureSync({
  form,
  values,
  chatListLength,
  setChatList,
  onFeaturesLoad,
}: UseClusterFeatureSyncParams) {
  const [chatVariables, setChatVariables] = useState<Variable[]>([])
  const supervisorVariables = values?.supervisor_config?.variables
  const openingStatement = values?.supervisor_config?.features?.opening_statement

  useEffect(() => {
    setChatVariables(
      values?.orchestration_mode === 'supervisor_loop'
        ? supervisorVariables || []
        : [],
    )
  }, [supervisorVariables, values?.orchestration_mode])

  useOpeningStatementSync({
    openingStatement,
    defaultModel: null,
    chatVariables,
    chatListLength,
    setChatList,
    resetOnSync: true,
  })

  const handleSaveChatVariable = (variables: Variable[]) => {
    setChatVariables(variables)
  }

  const handleSaveFeaturesConfig = (features: FeaturesConfigForm) => {
    form.setFieldValue(['supervisor_config', 'features'], features)
    onFeaturesLoad?.(features)

    const variables = supervisorVariables || []
    const nextVariables = appendMissingOpeningStatementVariables(
      features.opening_statement,
      variables,
    )
    if (nextVariables !== variables) {
      form.setFieldValue(['supervisor_config', 'variables'], nextVariables)
      setChatVariables(nextVariables)
    }
  }

  return {
    chatVariables,
    handleSaveChatVariable,
    handleSaveFeaturesConfig,
  }
}
