import type { ChatItem, Intervention } from '@/components/Chat/types'
import type { Variable } from '../Properties/VariableList/types'
import type { GraphRef } from '../../types'
import { triggerParams } from '../Properties/hooks/useVariableList'

// Shared version-array helpers (single source of truth).
import {
  mapLastVersion,
  flattenChatList,
  appendRegenerateVersion,
  applyFavorite,
  applyFeedback,
  removeMessageById,
  buildVersionMessages,
} from '@/components/Chat/utils/messageVersions'
export {
  mapLastVersion,
  flattenChatList,
  appendRegenerateVersion,
  applyFavorite,
  applyFeedback,
  removeMessageById,
  buildVersionMessages,
}

/**
 * Collects workflow input parameters from the configured variables and reports
 * any required-but-empty variable names.
 */
export const collectVariableParams = (variables: Variable[]) => {
  const params: Record<string, any> = {}
  const trigger_payload: Record<string, any> = {}
  const needRequired: string[] = []
  if (variables.length > 0) {
    const normalVariables = variables.filter(item => !item.nodeType)
    const webhookTriggerVariables = variables.filter(item => item.nodeType === 'webhook')
    normalVariables.forEach(vo => {
      params[vo.name] = vo.value ?? vo.defaultValue
      if (vo.required && (params[vo.name] === null || params[vo.name] === undefined || params[vo.name] === '')) {
        needRequired.push(vo.name)
      }
    })
    webhookTriggerVariables.forEach(vo => {
      const nameList = vo.name.split('.')
      if (!trigger_payload[nameList[0]]) {
        trigger_payload[nameList[0]] = {}
      }
      trigger_payload[nameList[0]][nameList[1]] = vo.value
    })
  }
  return { params, trigger_payload, needRequired, isCanSend: needRequired.length === 0 }
}

/**
 * Extracts variables from the workflow's start node and webhook trigger nodes,
 * merging in previously entered values.
 */
export const computeVariables = (graphRef: GraphRef, prevVariables: Variable[]): Variable[] => {
  const nodes = graphRef.current?.getNodes()
  const list = nodes?.map(node => node.getData()) || []
  const startNodes = list.filter(vo => vo.type === 'start')
  const webhookTriggerNodes = list.filter(vo => vo.type === 'trigger' && (vo.config?.trigger_type === 'webhook' || vo.config?.trigger_type?.defaultValue === 'webhook'))
  const allVariables: Variable[] = []
  if (startNodes.length) {
    const curVariables = startNodes[0].config.variables?.defaultValue

    curVariables.forEach((vo: Variable) => {
      if (typeof vo.default !== 'undefined') {
        vo.value = vo.default
      }
      const lastVo = prevVariables.find(item => item.name === vo.name)
      if (lastVo?.value) {
        vo.value = lastVo.value
      }
    })
    allVariables.push(...curVariables)
  }
  if (webhookTriggerNodes.length) {
    webhookTriggerNodes.forEach(webhookTrigger => {
      const webhookVariables: Variable[] = []
      Object.keys(triggerParams).forEach(key => {
        const params = Array.isArray(webhookTrigger.config?.[key])
            ? webhookTrigger.config?.[key]
            : Array.isArray(webhookTrigger.config?.[key].defaultValue)
            ? webhookTrigger.config?.[key].defaultValue
            : []
        params.forEach((param: any) => {
          if (param?.name) {
            webhookVariables.push({
              name: [triggerParams[key], param.name].join('.'),
              ui_type: 'paragraph',
              type: param.type || 'string',
              required: param.required || false,
              nodeType: 'webhook',
            })
          }
        })
      })
      if (webhookVariables.length) {
        webhookVariables.forEach((vo: Variable) => {
          const lastVo = prevVariables.find(item => item.name === vo.name)
          if (lastVo?.value) {
            vo.value = lastVo.value
          }
        })
        allVariables.push(...webhookVariables)
      }
    })
  }
  return allVariables
}

/**
 * Records the human-in-the-loop resolution on the matching intervention.
 */
export const applyInterventionResolved = (
  prev: Array<ChatItem | ChatItem[]>,
  node_id: string,
  fieldValues: Record<string, string>,
  actionId: string,
): Array<ChatItem | ChatItem[]> =>
  mapLastVersion(prev, (current) => {
    const interventions = current.interventions || []
    if (interventions.length === 0) return current
    const filterIndex = interventions.findIndex((item: Intervention) => item.node_id === node_id)
    if (filterIndex < 0) return current
    return {
      ...current,
      interventions: interventions.map((it: Intervention, idx: number) =>
        idx === filterIndex
          ? { ...it, resolved_form_data: fieldValues, resolved_action_id: actionId }
          : it
      ),
    }
  })

