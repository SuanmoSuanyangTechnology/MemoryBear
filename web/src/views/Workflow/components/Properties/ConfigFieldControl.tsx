import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Graph, Node } from '@antv/x6'
import { Input, Select, InputNumber, Switch, type FormInstance } from 'antd'

import type { NodeConfig } from '../../types'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
import type { Model } from '@/views/ModelManagement/types'
import type { Memory } from '@/views/MemoryManagement/types'
import Editor from '../Editor'
import CustomSelect from '@/components/CustomSelect'
import VariableSelect from './VariableSelect'
import CategoryList from './CategoryList'
import RbSlider from '@/components/RbSlider'
import ModelSelect from '@/components/ModelSelect'
import ActiveMemoryConfig from '@/components/ActiveMemoryConfig'

/**
 * Props for ConfigFieldControl
 */
interface ConfigFieldControlProps {
  configKey: string
  config: NodeConfig
  selectedNode: Node
  graphRef: React.MutableRefObject<Graph | undefined>
  form: FormInstance
  getVariables: (nodeType?: string, key?: string) => Suggestion[]
  activeMemoryConfig?: Memory | null
  setModelOptions: (models: Model[]) => void
  handleChangeModel: (value: string, option: any) => void
  handleChangeVariableList: (value: string, option: any, key: string) => void
}

/**
 * Renders the concrete form control for a generic config field, dispatching on
 * `config.type` (input / textarea / select / inputNumber / slider / modelSelect
 * / customSelect / activeMemoryConfig / variableList / switch / categoryList /
 * editor). Extracted from ConfigFieldRenderer to keep each file focused.
 */
const ConfigFieldControl: FC<ConfigFieldControlProps> = ({
  configKey: key,
  config,
  selectedNode,
  graphRef,
  form,
  getVariables,
  activeMemoryConfig,
  setModelOptions,
  handleChangeModel,
  handleChangeVariableList,
}) => {
  const { t } = useTranslation()

  return config.type === 'input'
    ? <Input placeholder={t('common.pleaseEnter')} />
    : config.type === 'textarea'
    ? <Input.TextArea placeholder={t('common.pleaseEnter')} />
    : config.type === 'select'
    ? <Select
        options={config.needTranslation ? (config.options || []).map(vo => ({ ...vo, label: t(vo.label) })) : config.options}
        placeholder={t('common.pleaseSelect')}
      />
    : config.type === 'inputNumber'
      ? <InputNumber
          placeholder={t('common.pleaseEnter')}
          className="rb:w-full!"
          onChange={(value) => form.setFieldValue(key, value)}
        />
      : config.type === 'slider'
      ? <RbSlider
          min={config.min}
          max={config.max}
          step={config.step || 0.01}
          isInput={true}
          size="small"
        />
      : config.type === 'modelSelect'
      ? <ModelSelect
          placeholder={t('common.pleaseSelect')}
          params={config.params}
          size="small"
          className="rb:w-full!"
          updateOptions={setModelOptions}
          onChange={handleChangeModel}
        />
      : config.type === 'customSelect'
      ? <CustomSelect
          placeholder={t('common.pleaseSelect')}
          url={config.url as string}
          params={config.params}
          hasAll={false}
          valueKey={config.valueKey}
          labelKey={config.labelKey}
          size="small"
        />
      : config.type === 'activeMemoryConfig'
      ? <ActiveMemoryConfig
          activeMemoryConfig={activeMemoryConfig}
          size="small"
        />
      : config.type === 'variableList'
      ? <VariableSelect
          placeholder={t(config.placeholder || 'common.pleaseSelect')}
          options={(() => {
            const baseVariableList = getVariables(selectedNode?.data?.type, key);
            // Apply filtering if specified in config
            if (config.filterNodeTypes) {
              return baseVariableList.filter(variable => {
                const nodeTypeMatch = !config.filterNodeTypes ||
                  (Array.isArray(config.filterNodeTypes) && config.filterNodeTypes.includes(variable.nodeData?.type));
                return nodeTypeMatch;
              });
            }
            if (config.onFilterVariableType) {
              const types = config.onFilterVariableType as string[];
              let list: Suggestion[] = []
              baseVariableList.forEach((variable) => {
                if (variable.children?.length) {
                  const filteredChildren = variable.children.filter((c: Suggestion) => types.includes(c.dataType));
                  if (filteredChildren.length > 0) {
                    list.push({ ...variable, children: filteredChildren });
                  } else if (types.includes(variable.dataType)) {
                    list.push({ ...variable, children: [] });
                  }
                } else if (types.includes(variable.dataType)) {
                  list.push(variable);
                }
              });

              return list
            }
            // Filter child nodes for iteration output
            if (config.filterChildNodes && selectedNode) {
              const graph = graphRef.current;
              if (!graph) return [];

              const nodes = graph.getNodes();

              // Find child nodes whose cycle field equals parent node's ID
              const childNodes = nodes.filter(node => {
                const nodeData = node.getData();
                return nodeData?.cycle === selectedNode.id;
              });

              return baseVariableList.filter(variable =>
                childNodes.some(node => node.id === variable.nodeData?.id) || selectedNode?.data?.type === 'iteration' && key === 'output' && variable.value.includes('sys.')
              );
            }
            return baseVariableList;
          })()}
          onChange={(value, option) => handleChangeVariableList(value as string, option, key)}
          size="small"
        />
      : config.type === 'switch'
      ? <Switch
          onChange={
            key === 'group'
              ? () => { form.setFieldValue('group_variables', []) }
              : key === 'vision'
                ? () => { form.setFieldValue('vision_input', undefined) }
                : undefined
          }
        />
      : config.type === 'categoryList'
      ? <CategoryList
          parentName={key}
          selectedNode={selectedNode}
          graphRef={graphRef}
          options={getVariables(selectedNode?.data?.type, key)}
        />
      : config.type === 'editor'
      ? <Editor options={getVariables(selectedNode?.data?.type, key)} variant="outlined" size="small" placeholder={config.placeholder || t('common.pleaseEnter')} />
      : null
}

export default ConfigFieldControl
