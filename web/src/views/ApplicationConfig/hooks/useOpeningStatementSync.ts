import { useEffect } from 'react'
import type { FormInstance } from 'antd'

import type { ChatData, ModelConfig } from '../types'
import type { Variable } from '../components/VariableList/types'
import type { Model } from '@/views/ModelManagement/types'
import { buildOpeningStatementMessage } from '@/components/Chat/openingStatement'

interface UseOpeningStatementSyncParams {
  /** Agent form instance */
  form: FormInstance
  /** Currently selected default model */
  defaultModel: Model | null
  /** Current chat variables */
  chatVariables: Variable[]
  /** Length of the debugging chat list */
  chatListLength: number
  /** Setter for the debugging chat list */
  setChatList: React.Dispatch<React.SetStateAction<ChatData[]>>
}

/**
 * Keep the first assistant message of each debugging session in sync with the
 * configured opening statement. Extracted from useAgent because it is a
 * self-contained side effect with a clear dependency set.
 */
export function useOpeningStatementSync({
  form,
  defaultModel,
  chatVariables,
  chatListLength,
  setChatList,
}: UseOpeningStatementSyncParams) {
  useEffect(() => {
    const opening_statement = form.getFieldValue(['features', 'opening_statement'])

    const assistantMsg = buildOpeningStatementMessage(opening_statement, { variables: chatVariables })
    if (assistantMsg) {
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
          } else if (vo.list && !Array.isArray(vo.list[0]) && vo.list[0].role === 'assistant') {
            vo.list[0] = assistantMsg
            return { ...vo, list: [...vo.list] }
          } else {
            return { ...vo, list: [assistantMsg, ...(vo.list || [])] }
          }
        })
      })
    }
  }, [defaultModel, chatListLength, form.getFieldValue(['features', 'opening_statement']), chatVariables])
}
