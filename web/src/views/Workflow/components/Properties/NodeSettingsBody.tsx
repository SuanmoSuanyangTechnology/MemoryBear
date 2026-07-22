import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Graph, Node } from '@antv/x6'
import { Form, Input, Select, Button, type FormInstance } from 'antd'

import type { NodeConfig } from '../../types'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
import type { Model } from '@/views/ModelManagement/types'
import type { Memory } from '@/views/MemoryManagement/types'
import type { Application } from '@/views/ApplicationManagement/types'
import HttpRequest from './HttpRequest'
import ToolConfig from './ToolConfig'
import OutputVariables from './OutputVariables'
import JinjaRender from './JinjaRender'
import CodeExecution from './CodeExecution'
import ListOperator from './ListOperator'
import NextStep from './NextStep'
import Trigger from './Trigger'
import HumanIntervention from './HumanIntervention'
import ConfigFieldRenderer from './ConfigFieldRenderer'
import { nodeLibrary } from '../../constant'

/**
 * Props for NodeSettingsBody
 */
interface NodeSettingsBodyProps {
  selectedNode: Node
  graphRef: React.MutableRefObject<Graph | undefined>
  data: any
  configs: Record<string, NodeConfig>
  values: any
  form: FormInstance
  variableList: Suggestion[]
  currentNodeVariables: Suggestion[]
  getVariables: (nodeType?: string, key?: string) => Suggestion[]
  appType?: Application['type']
  activeMemoryConfig?: Memory | null
  modelOptions: Model[]
  setModelOptions: (models: Model[]) => void
  advancedSettingsCollapsed: boolean
  setAdvancedSettingsCollapsed: (collapsed: boolean) => void
  handleChangeModel: (value: string, option: any) => void
  handleChangeVariableList: (value: string, option: any, key: string) => void
  handleSureReplace: () => void
  handleAddNode: (e: React.MouseEvent, portId?: string) => void
  nodeClick: ({ node }: { node: Node }) => void
}

/**
 * Renders the "setting" tab body of the properties panel.
 * Dispatches on the selected node type to the matching editor component, and
 * falls back to rendering each config key through ConfigFieldRenderer.
 * Extracted from Properties to keep the orchestrating component compact.
 */
const NodeSettingsBody: FC<NodeSettingsBodyProps> = ({
  selectedNode,
  graphRef,
  data,
  configs,
  values,
  form,
  variableList,
  currentNodeVariables,
  getVariables,
  appType,
  activeMemoryConfig,
  modelOptions,
  setModelOptions,
  advancedSettingsCollapsed,
  setAdvancedSettingsCollapsed,
  handleChangeModel,
  handleChangeVariableList,
  handleSureReplace,
  handleAddNode,
  nodeClick,
}) => {
  const { t } = useTranslation()

  return (
    <>
      <div className="rb:px-3!">
        <Form.Item name="id" label="ID">
          <Input disabled />
        </Form.Item>
        {selectedNode?.data?.type === 'human-intervention'
          ? <HumanIntervention
              options={getVariables(selectedNode?.data?.type)}
              selectedNode={selectedNode}
              graphRef={graphRef}
            />
          : selectedNode?.data?.type === 'list-operator'
          ? <ListOperator
            options={variableList}
            selectedNode={selectedNode}
          />
          : selectedNode?.data?.type === 'unknown'
          ? <>
            <Form.Item name="replaceNode" label={t('workflow.config.unknown.replaceNodeType')}>
              <Select
                options={nodeLibrary.map(category => ({
                  label: t(`workflow.${category.category}`),
                  options: category.nodes.filter(item => !['cycle-start', 'break'].includes(item.type)).map(node => ({
                    label: <div className="rb:flex rb:items-center rb:gap-2 rb:flex-1">
                      <div className={`rb:size-3.5 rb:bg-cover ${node.icon}`} />
                      <div className="rb:wrap-break-word rb:line-clamp-1">{t(`workflow.${node.type}`)}</div>
                    </div>,
                    value: node.type
                  }))
                }))}
                placeholder={t('common.pleaseSelect')}
                allowClear
              />
            </Form.Item>
            <Button type="primary" size="small" className="rb:text-[12px]!" onClick={handleSureReplace}>{t('workflow.sureReplace')}</Button>
          </>
          : selectedNode?.data?.type === 'trigger'
            ? <Trigger
              key={data.id || 'trigger'}
            />
            : selectedNode?.data?.type === 'http-request'
            ? <HttpRequest
              options={variableList}
              selectedNode={selectedNode}
              graphRef={graphRef}
            />
            : selectedNode?.data?.type === 'tool'
              ? <ToolConfig options={variableList} />
              : selectedNode?.data?.type === 'jinja-render'
                ? <JinjaRender
                  selectedNode={selectedNode}
                  options={getVariables(selectedNode?.data?.type, 'mapping')}
                  templateOptions={getVariables(selectedNode?.data?.type, 'template')}
                />
                : selectedNode?.data?.type === 'code'
                  ? <CodeExecution
                    graphRef={graphRef}
                    selectedNode={selectedNode}
                    options={getVariables(selectedNode?.data?.type, 'mapping')}
                  />
                  : configs && Object.keys(configs).length > 0 && Object.keys(configs).map((key) => (
                    <ConfigFieldRenderer
                      key={key}
                      configKey={key}
                      config={configs[key] || {}}
                      values={values}
                      selectedNode={selectedNode}
                      graphRef={graphRef}
                      form={form}
                      variableList={variableList}
                      getVariables={getVariables}
                      appType={appType}
                      activeMemoryConfig={activeMemoryConfig}
                      modelOptions={modelOptions}
                      setModelOptions={setModelOptions}
                      advancedSettingsCollapsed={advancedSettingsCollapsed}
                      setAdvancedSettingsCollapsed={setAdvancedSettingsCollapsed}
                      handleChangeModel={handleChangeModel}
                      handleChangeVariableList={handleChangeVariableList}
                    />
                  ))
        }

      {currentNodeVariables.length > 0 && !(!values?.group && selectedNode.getData().type === 'var-aggregator') &&
        <OutputVariables variables={currentNodeVariables} />
      }
      </div>
      <NextStep
        selectedNode={selectedNode}
        graphRef={graphRef}
        onAddNode={handleAddNode}
        onNodeClick={nodeClick}
        nodeData={data}
      />
    </>
  )
}

export default NodeSettingsBody
