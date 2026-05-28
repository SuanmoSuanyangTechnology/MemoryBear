/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-07 18:37:31 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-27 11:39:38
 */
import { type FC, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Flex, Form, Input, InputNumber, Select, Checkbox } from 'antd'
import { Node } from '@antv/x6'
import clsx from 'clsx'

import { nodeRun } from '@/api/application'
import RbCard from '@/components/RbCard/Card'
import styles from '../Properties/properties.module.css'
import ContextList from './ContextList'
import FileVarInput from './FileVarInput'
import RunResultDisplay from './RunResultDisplay'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
import type { Variable } from '../Properties/VariableList/types'
import CodeMirrorEditor from '@/components/CodeMirrorEditor';

interface RunResult {
  status: 'completed' | 'failed' | 'running';
  node_id?: string;
  node_type?: string;
  inputs?: Record<string, any>;
  outputs?: any;
  token_usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  process?: any;
  elapsed_time?: number;
  error?: string | null;
}

interface SingleNodeRunProps {
  open: boolean;
  onClose: () => void
  selectedNode: Node
  appId: string
  variableList: Suggestion[]
}

const SingleNodeRun: FC<SingleNodeRunProps> = ({ open, onClose, selectedNode, appId, variableList }) => {
  const { t } = useTranslation()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<RunResult | null>(null)

  const [isAutoRun, setIsAutoRun] = useState(false)

  const nodeData = selectedNode?.getData() || {}
  const nodeName = nodeData.name || t(`workflow.${nodeData.type}`)

  const isLlm = nodeData.type === 'llm'
  const hasContext = isLlm && nodeData.config.context.defaultValue

  // Recursively collect all {{nodeId.var}} references from nodeData, excluding conv. vars
  const extractVarRefs = (val: any, refs = new Set<string>()): Set<string> => {
    if (val?.type === 'start') {
      return refs
    }
    if (typeof val === 'string') {
      for (const m of val.matchAll(/\{\{([^}]+)\}\}/g))
        if (!m[1].startsWith('conv.') && m[1] !== 'context') {
          refs.add(m[1])
        }
    } else if (Array.isArray(val)) {
      val.forEach(v => extractVarRefs(v, refs))
    } else if (val && typeof val === 'object') {
      Object.values(val).forEach(v => extractVarRefs(v, refs))
    }
    return refs
  }

  const varRefs = extractVarRefs(nodeData)
  const visionInputRef = isLlm ? nodeData.config.vision_input?.defaultValue?.match(/\{\{([^}]+)\}\}/)?.[1] : undefined
  const contextInputRef = isLlm ? nodeData.config.context?.defaultValue?.match(/\{\{([^}]+)\}\}/)?.[1] : undefined
  const inputVars: Suggestion[] = nodeData?.type === 'start'
    ? (nodeData.config?.variables?.defaultValue || []).map((item: Variable) => ({
      label: item.description,
      value: item.name,
      dataType: item.type,
      nodeData,
      options: item.options || [],
      ui_type: item.ui_type || 'text-input',
      required: item.required,
    }))
    : variableList.filter(v => varRefs.has(v.value) && v.value !== visionInputRef && v.value !== contextInputRef)


  const handleRun = () => {
    form.validateFields()
      .then((values) => {
        const { inputs = {} } = values
        console.log('values', values)
        const params: Record<string, any> = {};
        Object.keys(inputs).forEach(key => {
          const value = inputs[key]

          if (typeof value === 'object') {
            params[key] = value.map((file: any) => {
              if (file.url) {
                return file
              } else {
                return {
                  type: file.type,
                  transfer_method: 'local_file',
                  upload_file_id: file.response.data.file_id
                }
              }
            })
          } else {
            params[key] = value;
          }
        })
        setLoading(true)
        setResult({ status: 'running' })

        if (hasContext) {
          const contextValues: string[] = form.getFieldValue('context') || []
          if (contextValues.length > 0) {
            params['context'] = contextValues.map(item => { try { return JSON.parse(item) } catch { return item } })
          }
        }

        nodeRun(appId, nodeData.id, { inputs: params, stream: false })
          .then(res => {
            setResult(res as RunResult)
          })
          .catch(err => {
            setResult({ status: 'failed', error: err.message })
            setLoading(false)
          })
          .finally(() => setLoading(false))
      })
  }

  useEffect(() => {
    if (open) {
      if (nodeData?.type === 'iteration' || inputVars.length < 1 && !hasContext && !(isLlm && nodeData?.config?.vision?.defaultValue)) {
        setIsAutoRun(true)
      }
    }
  }, [open, inputVars, isLlm, hasContext, nodeData?.type, nodeData?.config?.vision?.defaultValue])

  useEffect(() => {
    if (isAutoRun) {
      handleRun()
    }
  }, [isAutoRun])

  if (!open) return null

  return (
    // 与 Properties 完全相同的定位容器
    <div className={clsx('rb:h-[calc(100vh-88px)] rb:w-90 rb:absolute rb:right-0 rb:top-0 rb:bottom-2.5 rb:z-1002', styles.properties)}>
      {/* mask：仅覆盖 header 以下的区域，header 保持透明露出节点名 */}
      <div
        className="rb:absolute rb:inset-x-0 rb:bottom-0 rb:top-0 rb:rounded-xl rb:bg-[rgba(0,0,0,0.3)] rb:z-1002"
      />

      {/* SingleNodeRun 卡片，z-index 高于 mask */}
      <div className="rb:absolute rb:inset-x-0 rb:top-25.5 rb:bottom-0 rb:z-1003">
        <RbCard
          title={`${t('workflow.testRun')} ${nodeName}`}
          extra={
            <div
              className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/close.svg')]"
              onClick={onClose}
            />
          }
          headerType="borderless"
          headerClassName="rb:font-[MiSans-Bold] rb:font-bold rb:min-h-[48px]!"
          className="rb:h-full! rb:hover:shadow-none!"
          bodyClassName="rb:overflow-y-auto! rb:h-[calc(100%-48px)]! rb:px-3! rb:pt-0! rb:pb-3!"
        >
          <Form form={form} layout="vertical" size="small" className="rb:mb-0!">
            <Flex vertical gap={12}>
              {/* Variables */}
              {nodeData?.type !== 'iteration' && inputVars.length > 0 && (
                <Flex vertical gap={8}>
                  <div className="rb:text-[12px] rb:font-medium rb:text-[#5B6167]">{t('workflow.variables')}</div>
                  {inputVars.map((v: Suggestion) => (
                    <Form.Item
                      key={v.value}
                      name={['inputs', v.value.replace('{{', '').replace('}}', '')]}
                      label={v.dataType.includes('boolean')
                        ? null
                        : <Flex gap={4} align="center" className="rb:text-[12px]">
                          {v.nodeData?.icon && <div className={`rb:size-3.5 rb:bg-cover ${v.nodeData.icon}`} />}
                          <span className="rb:font-medium">{v.nodeData?.name}</span>
                          <span className="rb:text-[#5B6167]">/</span>
                          <span className="rb:text-[#1677ff]">{v.label}</span>
                        </Flex>
                      }
                      rules={['start'].includes(nodeData.type) && v.ui_type !== 'checkbox' ? [{
                        required: v.required,
                        message: v.ui_type === 'select' && Array.isArray(v.options) && v.options.length > 0
                          ? t('common.selectPlaceholder', { title: v.label })
                          : t('common.inputPlaceholder', { title: v.label })
                      }] : undefined}
                      className="rb:mb-0!"
                    >
                      {v.dataType === 'object'
                        ? <CodeMirrorEditor
                            language="json"
                            variant="outlined"
                          />
                        : v.ui_type && ['select'].includes(v.ui_type) && Array.isArray(v.options) && v.options.length > 0
                          ? <Select
                            placeholder={t('common.pleaseSelect')}
                            options={v.options.map((item: string) => ({ label: item, value: item }))}
                          />
                        : ['array[string]', 'array[number]'].includes(v.dataType) && Array.isArray(v.default) && v.default.length > 0
                          ? <Select
                            placeholder={t('common.pleaseSelect')}
                            options={v.default.map((item: string) => ({ label: item, value: item }))}
                          />
                        : v.dataType.includes('string') && nodeData.type === 'knowledge-retrieval'
                          ? <Input.TextArea
                            placeholder={t('common.pleaseEnter')}
                            size="small"
                          />
                        : v.dataType.includes('string')
                          ? <Input
                            placeholder={t('common.pleaseEnter')}
                            size="small"
                          />
                        : v.dataType.includes('number')
                          ? <InputNumber
                            size="small"
                            placeholder={t('common.pleaseEnter')}
                            className="rb:w-full!"
                            onChange={(value) => form.setFieldValue(['retry', 'retry_interval'], value)}
                          />
                        : v.dataType.includes('file')
                          ? <FileVarInput
                            name={['inputs', v.value.replace('{{', '').replace('}}', '')]}
                            dataType={v.dataType}
                            form={form}
                          />
                        : v.dataType.includes('boolean')
                          ? <Checkbox>
                            <Flex gap={4} align="center" className="rb:text-[12px]">
                            {v.nodeData?.icon && <div className={`rb:size-3.5 rb:bg-cover ${v.nodeData.icon}`} />}
                            <span className="rb:font-medium">{v.nodeData?.name}</span>
                            <span className="rb:text-[#5B6167]">/</span>
                            <span className="rb:text-[#1677ff]">{v.label}</span>
                          </Flex>
                          </Checkbox>
                          : null
                      }
                    </Form.Item>
                  ))}
                </Flex>
              )}
              {/* Context */}
              {hasContext && <ContextList />}

              {isLlm && nodeData?.config?.vision?.defaultValue && (() => {
                const ref = nodeData.config.vision_input?.defaultValue
                const visionVar = ref ? variableList.find(v => v.value === ref) : undefined
                const dataType = visionVar?.dataType ?? 'array[file]'

                return (
                  <Form.Item
                    name={['inputs', ref.replace('{{', '').replace('}}', '')]}
                    label={t('workflow.config.llm.vision')}
                    className="rb:mb-0!"
                  >
                    <FileVarInput name={['inputs', ref.replace('{{', '').replace('}}', '')]} dataType={dataType} form={form} />
                  </Form.Item>
                )
              })()}

              {/* Run button */}
              {(!isAutoRun || result?.status) &&
                <Button type="primary" block onClick={handleRun} loading={!result?.status && loading} disabled={loading}>
                  {result?.status ? t('workflow.reStartRun') : t('workflow.startRun')}
                </Button>
              }

              <RunResultDisplay result={result} loading={loading} nodeData={nodeData} />
            </Flex>
          </Form>
        </RbCard>
      </div>
    </div>
  )
}

export default SingleNodeRun
