import { useState, useCallback, useEffect, useRef, type FC } from 'react'
import { Popover, Flex } from 'antd'
import { WarningFilled } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { Node } from '@antv/x6';

import type { WorkflowRef } from '@/views/ApplicationConfig/types'
import { nodeLibrary } from '../../constant'
import { getToolMethods } from '@/api/tools'
import RbDrawer from '@/components/RbDrawer'
import { useWorkflowStore } from '@/store/workflow'

interface CheckListProps {
  workflowRef: React.RefObject<WorkflowRef>
  appId: string
}

export interface CheckError {
  key: string
  message: string
}

export interface NodeCheckResult {
  id: string
  name: string
  type: string
  icon: string
  errors: CheckError[]
}

const allNodes = nodeLibrary.flatMap(c => c.nodes)
const nodeIconMap: Record<string, string> = Object.fromEntries(allNodes.map(n => [n.type, n.icon]))
const nodeConfigMap: Record<string, Record<string, any>> = Object.fromEntries(
  allNodes.filter(n => n.config).map(n => [n.type, n.config!])
)

// Special validators for fields that need deeper checks beyond simple empty check
const specialValidators: Record<string, (val: any) => boolean> = {
  // llm.messages: at least one message with non-empty content
  'llm.messages': (val: any[]) => !Array.isArray(val) || !val.some(m => m?.content && String(m.content).trim()),
  // knowledge-retrieval.knowledge_retrieval: knowledge_bases array must be non-empty
  'knowledge-retrieval.knowledge_retrieval': (val: any) => !(val?.knowledge_bases?.length > 0),
  'memory-write.messages': (val: any[]) => !Array.isArray(val) || !val.some(m => m?.content && String(m.content).trim()),
  // if-else.cases: every case must have at least one expression, and every expression must be fully set
  'if-else.cases': (val: any[]) => {
    if (!Array.isArray(val) || !val.length) return true
    return val.some(c => {
      if (!c?.expressions?.length) return true
      return c.expressions.some((expr: any) => {
        if (!expr?.left) return true
        if (['not_empty', 'empty'].includes(expr.operator)) return false
        return !(!!expr.left && (!!expr.right || typeof expr.right === 'boolean' || typeof expr.right === 'number'))
      })
    })
  },
  // question-classifier.categories: every category must have a value
  'question-classifier.categories': (val: any[]) => !Array.isArray(val) || !val.some(c => c?.class_name && String(c.class_name).trim()),
  // var-aggregator.group_variables: must be non-empty array
  'var-aggregator.group_variables': (val: any[]) => !Array.isArray(val) || !val.length,
  // assigner.assignments: every item needs variable_selector + operation; value required unless operation is 'clear'
  'assigner.assignments': (val: any[]) => {
    if (!Array.isArray(val) || !val.length) return false
    return val.some(a => {
      if (!a?.variable_selector || !a?.operation) return true
      if (a.operation === 'clear') return false
      return a.value === undefined || a.value === null || a.value === ''
    })
  },
  // http-request.body: binary content_type requires data
  'http-request.body': (val: any) => val?.content_type === 'binary' && !val?.data,
  // tool.tool_parameters: validated async via API, placeholder always returns false
  'tool.tool_parameters': () => false,
  // code.input_variables: if non-empty, every item must have both name and variable
  'code.input_variables': (val: any[]) => Array.isArray(val) && val.length > 0 && val.some(v => !v?.name || !v?.variable),
  // code.output_variables: must be non-empty
  'code.output_variables': (val: any[]) => !Array.isArray(val) || !val.length,
  // jinja-render.mapping: if non-empty, every item must have a name
  'jinja-render.mapping': (val: any[]) => Array.isArray(val) && val.length > 0 && val.some(v => !v?.name || !v?.value),
}

function isEmpty(val: any): boolean {
  console.log('validateNode isEmpty', val, val === undefined || val === null || val === '')
  if (val === undefined || val === null || val === '') return true
  if (Array.isArray(val)) return val.length === 0
  return false
}

function validateNode(type: string, config: Record<string, any>): CheckError[] {
  const errors: CheckError[] = []
  const nodeConfig = nodeConfigMap[type]
  if (!nodeConfig) return errors

  const get = (key: string) => config[key]?.defaultValue

  Object.entries(nodeConfig).forEach(([field, fieldConfig]) => {
    if (!fieldConfig?.required) return
    const val = get(field)
    const specialKey = `${type}.${field}`
    const specialValidator = specialValidators[specialKey]
    const isInvalid = specialValidator ? specialValidator(val) : isEmpty(val)
    console.log('validateNode', val, specialKey, specialValidator, isEmpty(val))
    if (isInvalid) errors.push({ key: specialKey, message: '' })
  })

  // http-request body.data (binary) — not a top-level required field, check separately
  if (type === 'http-request') {
    const body = get('body')
    if (body?.content_type === 'binary' && !body?.data) {
      errors.push({ key: 'http-request.body.data', message: '' })
    }
  }

  // console.log('nodeConfig', nodeConfigMap, nodeConfig, errors)
  return errors
}

export async function runCheckOnGraph(
  graph: import('@antv/x6').Graph,
  t: (key: string) => string
): Promise<NodeCheckResult[]> {
  const nodes = graph.getNodes()
  const edges = graph.getEdges()
  const targetIds = new Set<string>()
  const childTargetIds = new Set<string>()
  edges.forEach(e => {
    targetIds.add(e.getTargetCellId())
    const srcData = graph.getCellById(e.getSourceCellId())?.getData()
    const tgtData = graph.getCellById(e.getTargetCellId())?.getData()
    if (srcData?.cycle && tgtData?.cycle && srcData.cycle === tgtData.cycle) {
      childTargetIds.add(e.getTargetCellId())
    }
  })

  const checked: NodeCheckResult[] = []
  for (const node of nodes) {
    const data = node.getData()
    if (!data || ['add-node', 'notes', 'cycle-start', 'break'].includes(data.type)) continue

    const errors: CheckError[] = []
    const isChildNode = !!data.cycle
    const hasIncoming = isChildNode ? childTargetIds.has(node.id) : !['start', 'cycle-start'].includes(data.type) ? targetIds.has(node.id) : true
    if (!hasIncoming) errors.push({ key: 'notConnected', message: t('workflow.notConnected') })

    const configErrors = validateNode(data.type, data.config ?? {})
    configErrors.forEach(e => {
      errors.push({ key: e.key, message: `${t(`workflow.checkListErrors.${e.key}`)} ${t('workflow.cannotBeEmpty')}`.trim() })
    })

    if (data.type === 'tool') {
      const toolId = data.config?.tool_id?.defaultValue ?? data.config?.tool_id
      const toolParameters = data.config?.tool_parameters?.defaultValue ?? data.config?.tool_parameters ?? {}
      if (toolId) {
        try {
          const methods = await getToolMethods(toolId) as Array<{ name: string; parameters: Array<{ name: string; required: boolean }> }>
          const operation = toolParameters?.operation
          const method = operation ? methods.find(m => m.name === operation) : methods[0]
          if (method) {
            method.parameters
              .filter(p => p.required && (toolParameters[p.name] === undefined || toolParameters[p.name] === null || toolParameters[p.name] === ''))
              .forEach(p => errors.push({ key: 'tool.tool_parameters', message: `${p.name} ${t('workflow.cannotBeEmpty')}` }))
          }
        } catch { /* ignore */ }
      }
    }

    if (errors.length) {
      checked.push({ id: node.id, name: data.name || t(`workflow.${data.type}`), type: data.type, icon: nodeIconMap[data.type] ?? '', errors })
    }
  }
  return checked
}

const CheckList: FC<CheckListProps> = ({ workflowRef, appId }) => {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const { setCheckResults, getCheckResults } = useWorkflowStore()
  const results = getCheckResults(appId)
  const timerRef = useRef<ReturnType<typeof setTimeout>>()

  const runCheck = useCallback(async () => {
    const graph = workflowRef.current?.graphRef?.current
    if (!graph) return []

    const nodes = graph.getNodes()
    const edges = graph.getEdges()
    const sourceIds = new Set<string>()
    const targetIds = new Set<string>()
    // child-to-child edges within same parent (cycle)
    const childTargetIds = new Set<string>()
    edges.forEach(e => {
      sourceIds.add(e.getSourceCellId())
      targetIds.add(e.getTargetCellId())
      const srcData = graph.getCellById(e.getSourceCellId())?.getData()
      const tgtData = graph.getCellById(e.getTargetCellId())?.getData()
      if (srcData?.cycle && tgtData?.cycle && srcData.cycle === tgtData.cycle) {
        childTargetIds.add(e.getTargetCellId())
      }
    })

    const checked: NodeCheckResult[] = []
    for (const node of nodes) {
      const data = node.getData()
      if (!data || ['add-node', 'notes', 'cycle-start', 'break'].includes(data.type)) continue

      const errors: CheckError[] = []


      // Check connectivity
      const isChildNode = !!data.cycle
      const hasIncoming = isChildNode ? childTargetIds.has(node.id) : !['start', 'cycle-start'].includes(data.type) ? targetIds.has(node.id) : true
      if (!hasIncoming) {
        errors.push({ key: 'notConnected', message: t('workflow.notConnected') })
      }

      // Validate config
      const configErrors = validateNode(data.type, data.config ?? {})
      configErrors.forEach(e => {
        errors.push({ key: e.key, message: `${t(`workflow.checkListErrors.${e.key}`)} ${t('workflow.cannotBeEmpty')}`.trim() })
      })

      // Tool node: fetch parameters via API and check required fields
      if (data.type === 'tool') {
        const toolId = data.config?.tool_id?.defaultValue ?? data.config?.tool_id
        const toolParameters = data.config?.tool_parameters?.defaultValue ?? data.config?.tool_parameters ?? {}
        if (toolId) {
          try {
            const methods = await getToolMethods(toolId) as Array<{ name: string; parameters: Array<{ name: string; required: boolean }> }>
            const operation = toolParameters?.operation
            const method = operation ? methods.find(m => m.name === operation) : methods[0]
            if (method) {
              const missingParams = method.parameters.filter(p => p.required && (toolParameters[p.name] === undefined || toolParameters[p.name] === null || toolParameters[p.name] === ''))
              missingParams.forEach(p => errors.push({ key: 'tool.tool_parameters', message: `${p.name} ${t('workflow.cannotBeEmpty')}` }))
            }
          } catch {
            // ignore API errors
          }
        }
      }

      if (errors.length) {
        checked.push({
          id: node.id,
          name: data.name || t(`workflow.${data.type}`),
          type: data.type,
          icon: nodeIconMap[data.type] ?? '',
          errors,
        })
      }
    }

    return checked
  }, [workflowRef.current?.graphRef?.current, t])

  const scheduleCheck = useCallback(() => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(async () => {
      setCheckResults(appId, await runCheck())
    }, 500)
  }, [runCheck])

  useEffect(() => {
    const graph = workflowRef.current?.graphRef?.current
    if (!graph) return
    const events = ['node:added', 'node:removed', 'node:change:data', 'edge:added', 'edge:removed']
    events.forEach(e => graph.on(e, scheduleCheck))
    scheduleCheck()
    return () => {
      events.forEach(e => graph.off(e, scheduleCheck))
      clearTimeout(timerRef.current)
    }
  }, [workflowRef.current?.graphRef?.current])

const handleOpen = () => {
    setOpen(true)
  }

  const focusNode = (id: string) => {
    const graph = workflowRef.current?.graphRef?.current
    if (!graph) return
    const node = graph.getCellById(id)
    if (node) {
      workflowRef.current?.nodeClick({node} as { node: Node })
    }
    setOpen(false)
  }

  return (
    <>
      <Popover content={t('workflow.checkList')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
        <div className="rb:relative rb:cursor-pointer rb:size-7.5" onClick={handleOpen}>
          <div className="rb:size-7.5 rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6] rb:rounded-[10px] rb:bg-[url('@/assets/images/workflow/checkList.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat" />
          {results.length > 0 && (
            <span className="rb:absolute rb:-top-1 rb:-right-1 rb:min-w-3.5 rb:h-3.5 rb:px-0.5 rb:bg-[#F04438] rb:text-white rb:text-[9px] rb:leading-3.5 rb:rounded-full rb:flex rb:items-center rb:justify-center">
              {results.reduce((sum, n) => sum + n.errors.length, 0)}
            </span>
          )}
        </div>
      </Popover>
      <RbDrawer
        title={
          <span className="rb:text-[16px] rb:font-semibold">
            {t('workflow.checkList')}{results.length > 0 ? `(${results.reduce((sum, n) => sum + n.errors.length, 0)})` : ''}
          </span>
        }
        open={open}
        onClose={() => setOpen(false)}
        width={360}
        styles={{ body: { padding: '12px 16px' } }}
      >
        <p className="rb:text-[12px] rb:text-[#5B6167] rb:mb-3">{t('workflow.checkListDesc')}</p>
        {results.length === 0
          ? <div className="rb:text-center rb:text-[#5B6167] rb:text-[13px] rb:py-8">{t('workflow.checkListEmpty')}</div>
          : <Flex vertical gap={8} className="rb:pb-3!">
            {results.map(node => (
              <div key={node.id} className="rb-border rb:rounded-lg">
                <Flex align="center" gap={8} className="rb:px-3! rb:py-2.5! rb-border-b">
                  <div className={`rb:size-5 rb:rounded-md rb:bg-size-[14px_14px] rb:bg-center rb:bg-no-repeat ${node.icon}`} />
                  <span className="rb:text-[13px] rb:font-medium rb:flex-1 rb:truncate">{node.name}</span>
                  <span
                    className="rb:text-[12px] rb:text-[#155EEF] rb:cursor-pointer rb:whitespace-nowrap"
                    onClick={() => focusNode(node.id)}
                  >
                    {t('workflow.goto')} →
                  </span>
                </Flex>

                <Flex vertical gap={4} className="rb:px-3! rb:py-2!">
                  {node.errors.map((err, i) => (
                    <Flex key={i} align="center" gap={6}>
                      <WarningFilled className="rb:text-[#FF5D34]! rb:text-[12px] rb:shrink-0" />
                      <span className="rb:text-[12px] rb:text-[#5B6167]">{err.message}</span>
                    </Flex>
                  ))}
                </Flex>
              </div>
            ))}
          </Flex>
        }
      </RbDrawer>
    </>
  )
}

export default CheckList
