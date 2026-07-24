import type { FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Form, Input, Select, InputNumber, Switch } from 'antd'

import type { NodeConfig } from '../../types'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
import CustomSelect from '@/components/CustomSelect'
import RbSlider from '@/components/RbSlider'
import ModelSelect from '@/components/ModelSelect'
import ActiveMemoryConfig from '@/components/ActiveMemoryConfig'
import Editor from '../Editor'
import VariableSelect from './VariableSelect'
import CategoryList from './CategoryList'
import { useProperties } from './PropertiesContext'

interface BasicFieldProps {
  /** Configuration key */
  configKey: string
  /** Configuration item */
  config: NodeConfig
}

/**
 * Renders a "basic" node configuration field wrapped in a Form.Item.
 * Handles the primitive control types: input, textarea, select, inputNumber,
 * slider, modelSelect, customSelect, activeMemoryConfig, variableList, switch,
 * categoryList and editor.
 */
const BasicField: FC<BasicFieldProps> = ({ configKey: key, config }) => {
  const { t } = useTranslation()
  const {
    form,
    values,
    data,
    selectedNode,
    graphRef,
    activeMemoryConfig,
    setModelOptions,
    getFilteredVariableList,
  } = useProperties()

  /**
   * Handle variable list change and update output type for iteration nodes
   * @param _value - Selected value
   * @param option - Selected option
   * @param changeKey - Configuration key
   */
  const handleChangeVariableList = (_value: string, option: any, changeKey: string) => {
    if (selectedNode?.data?.type === 'iteration' && changeKey === 'output') {
      form.setFieldValue('output_type', option?.dataType)
    }
  }

  const handleChangeModel = (_value: string, option: any) => {
    if (!option?.capability?.includes('function_call') && data.type === 'parameter-extractor') {
      form.setFieldValue('inference_mode', 'prompt')
    }
  }

  return (
    <Form.Item
      key={key}
      name={key}
      label={key === 'vision_input'
        ? undefined : key === 'parallel_count'
          ? <span className="rb:text-[10px] rb:text-[#5B6167] rb:leading-3.5 rb:-mb-1!">{t(`workflow.config.${selectedNode?.data?.type}.${key}`)}</span>
          : t(`workflow.config.${selectedNode?.data?.type}.${key}`)
      }
      tooltip={config.tip ? t(config.tip) : undefined}
      layout={config.type === 'switch' ? 'horizontal' : 'vertical'}
      className={
        key === 'parallel' && values?.parallel
          ? 'rb:mb-1!'
          : key === 'vision' && values?.vision
            ? 'rb:mb-2!'
            : key === 'group' && values?.group
              ? 'rb:mb-3!'
              : ''
      }
      hidden={Boolean(config.hidden)}
      required={config.required}
    >
      {config.type === 'input'
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
            const baseVariableList = getFilteredVariableList(selectedNode?.data?.type, key);
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
        ? <Switch onChange={
          key === 'group'
            ? () => { form.setFieldValue('group_variables', []) }
            : key === 'vision'
              ? () => { form.setFieldValue('vision_input', undefined) }
              : undefined
        } />
        : config.type === 'categoryList'
        ? <CategoryList
          parentName={key}
          selectedNode={selectedNode}
          graphRef={graphRef}
          options={getFilteredVariableList(selectedNode?.data?.type, key)}
        />
        : config.type === 'editor'
        ? <Editor options={getFilteredVariableList(selectedNode?.data?.type, key)} variant="outlined" size="small" placeholder={config.placeholder || t('common.pleaseEnter')} />
        : null
      }
    </Form.Item>
  )
}

export default BasicField
