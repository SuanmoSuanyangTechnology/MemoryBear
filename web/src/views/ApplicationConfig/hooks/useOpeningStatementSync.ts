import { useEffect, useMemo, type Dispatch, type SetStateAction } from 'react'

import type { ChatData, FeaturesConfigForm, ModelConfig } from '../types'
import type { Variable } from '../components/VariableList/types'
import type { Model } from '@/views/ModelManagement/types'
import { buildOpeningStatementMessage } from '@/components/Chat/openingStatement'

interface UseOpeningStatementSyncParams {
  /** Opening statement configuration to synchronize */
  openingStatement?: FeaturesConfigForm['opening_statement']
  /** Currently selected default model */
  defaultModel: Model | null
  /** Current chat variables */
  chatVariables: Variable[]
  /** Length of the debugging chat list */
  chatListLength: number
  /** Setter for the debugging chat list */
  setChatList: Dispatch<SetStateAction<ChatData[]>>
  /** Whether each configuration change should rebuild the entire chat list */
  resetOnSync?: boolean
}

/**
 * Keep the opening statement of each debugging session synchronized with the
 * configured content and runtime variables.
 */
export function useOpeningStatementSync({
  openingStatement,
  defaultModel,
  chatVariables,
  chatListLength,
  setChatList,
  resetOnSync = false,
}: UseOpeningStatementSyncParams) {
  const assistantMsg = useMemo(
    () => buildOpeningStatementMessage(openingStatement, { variables: chatVariables }),
    [openingStatement, chatVariables],
  )

  useEffect(() => {
    if (resetOnSync) {
      setChatList([{ list: assistantMsg ? [assistantMsg] : [] }])
      return
    }
    if (!assistantMsg) return

    setChatList(prev => {
      if (prev.length === 0 && !defaultModel) return prev
      if (defaultModel && prev.length === 1) {
        return [{
          label: defaultModel.name,
          model_config_id: defaultModel.id,
          model_parameters: defaultModel.config as unknown as ModelConfig,
          list: [assistantMsg]
        }]
      }

      return prev.map(vo => {
        if (vo.list?.length === 0) {
          return { ...vo, list: [assistantMsg] }
        }
        if (vo.list && !Array.isArray(vo.list[0]) && vo.list[0].role === 'assistant') {
          return { ...vo, list: [assistantMsg, ...vo.list.slice(1)] }
        }
        return { ...vo, list: [assistantMsg, ...(vo.list || [])] }
      })
    })
  }, [assistantMsg, defaultModel, chatListLength, resetOnSync, setChatList])
}
