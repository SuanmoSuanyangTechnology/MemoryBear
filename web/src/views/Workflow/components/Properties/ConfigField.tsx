import type { FC } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { Form, Flex } from 'antd'

import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
import type { LexicalEditorProps } from '../Editor'
import Knowledge from '@/components/Knowledge'
import MessageEditor from './MessageEditor'
import ParamsList from './ParamsList'
import GroupVariableList from './GroupVariableList'
import CaseList from './CaseList'
import ConditionList from './ConditionList'
import CycleVarsList from './CycleVarsList'
import AssignmentList from './AssignmentList'
import MemoryConfig from './MemoryConfig'
import VariableList from './VariableList'
import ModelConfig from './ModelConfig'
import MappingList from './MappingList'
import ErrorHandle from './ErrorHandle'
import RadioGroupBtn from './RadioGroupBtn'
import Retry from './Retry'
import ToolList from './ToolList'
import MetadataFilter from './MetadataFilter'
import BasicField from './BasicField'
import { useProperties } from './PropertiesContext'

interface ConfigFieldProps {
  /** Configuration key */
  configKey: string
}

/**
 * Dispatches a single node configuration field to the right control.
 * Handles all the "complex" config types and special-cased keys, and falls
 * back to `BasicField` for primitive controls.
 */
const ConfigField: FC<ConfigFieldProps> = ({ configKey: key }) => {
  const { t } = useTranslation()
  const {
    configs,
    values,
    selectedNode,
    graphRef,
    appType,
    variableList,
    advancedSettingsCollapsed,
    setAdvancedSettingsCollapsed,
    modelOptions,
    getFilteredVariableList,
  } = useProperties()

  const config = configs[key] || {}

  if (config.dependsOn && (values as any)?.[config.dependsOn as string] !== config.dependsOnValue) {
    return null
  }

  if (selectedNode?.data?.type === 'start' && key === 'variables' && config.type === 'define') {
    return (
      <Form.Item key={key} name={key} className="rb:mb-0!">
        <VariableList
          parentName={key}
          selectedNode={selectedNode}
        />
      </Form.Item>
    )
  }

  if ((key === 'model_id' && selectedNode?.data?.type === 'llm')
    || (key === 'model' && selectedNode?.data?.type === 'agent')
  ) {
    return (
      <ModelConfig
        key={key}
        parentName={selectedNode?.data?.type === 'agent' ? key : undefined}
        variableOptions={getFilteredVariableList(selectedNode?.data?.type)}
        hideStructuredOutputConfig={!(key === 'model_id' && selectedNode?.data?.type === 'llm')}
      />
    )
  }
  if (selectedNode?.data?.type === 'llm' && key === 'messages' && config.type === 'define') {
    // 为llm节点且isArray=true时添加context变量支持
    let contextVariableList = [...getFilteredVariableList('llm')];
    const isArrayMode = config.isArray !== false; // 默认为true

    if (isArrayMode) {
      const contextKey = `${selectedNode.id}_context`;
      const hasContextVariable = contextVariableList.some(v => v.key === contextKey);

      if (!hasContextVariable) {
        contextVariableList.unshift({
          key: contextKey,
          label: 'context',
          type: 'variable',
          dataType: 'string',
          value: `context`,
          nodeData: selectedNode.getData(),
          isContext: true,
        });
      }
    }
    return (
      <Form.Item key={key} name={key}>
        <MessageEditor
          key={key}
          options={contextVariableList.filter(variable => variable.nodeData?.type !== 'knowledge-retrieval')}
          parentName={key}
          placeholder={t(config.placeholder || 'common.pleaseSelect')}
          size="small"
        />
      </Form.Item>
    )
  }
  if (selectedNode?.data?.type === 'iteration' && key === 'output_type') {
    return (<Form.Item key={key} name={key} hidden />)
  }
  if (key === 'inference_mode') {
    const modelCapability: string[] = modelOptions.find((item) => item.id === values?.model_id)?.capability || []
    const options = modelCapability.includes('function_call') && config.options
      ? [...config.options]
      : config.options
      ? config.options.filter((item) => item.value !== 'function_calling')
      : []

    return (
      <div key={key} className="rb:text-[12px] rb:leading-4.5">
        <Flex align="center" className="rb:font-medium rb:cursor-pointer rb:mb-2!" onClick={() => setAdvancedSettingsCollapsed(!advancedSettingsCollapsed)}>
          {t('workflow.config.parameter-extractor.advanced_settings')}
          <div
            className={clsx("rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/caret_right_outlined.svg')]", {
              'rb:rotate-90': !advancedSettingsCollapsed
            })}
          ></div>
        </Flex>
        <Form.Item
          name="inference_mode"
          label={t('workflow.config.parameter-extractor.inference_mode')}
          hidden={!advancedSettingsCollapsed}
          tooltip={t('workflow.config.parameter-extractor.inference_mode_tip')}
        >
          <RadioGroupBtn
            options={options.map((item) => ({
              ...item,
              label: t(item.label)
            }))}
            type="outer"
            allowClear={false}
          />
        </Form.Item>
      </div>
    )
  }
  if (config.type === 'define') {
    return null
  }

  if (config.type === 'retry') {
    return (
      <Retry key={key} />
    )
  }
  if (config.type === 'knowledge') {
    return (
      <Form.Item
        key={key}
        name={key}
      >
        <Knowledge variant="workflow" />
      </Form.Item>
    )
  }
  if (config.type === 'metadata') {
    return (
      <MetadataFilter
        options={variableList}
      />
    )
  }

  if (config.type === 'messageEditor') {
    return (
      <Form.Item key={key} name={key} required={config.required} label={selectedNode?.data?.type === 'memory-write' ? t(`workflow.config.${selectedNode?.data?.type}.${key}`) : undefined}>
        <MessageEditor
          title={t(`workflow.config.${selectedNode?.data?.type}.${key}`)}
          placeholder={t(config.placeholder || 'common.pleaseEnter')}
          isArray={!!config.isArray}
          parentName={key}
          language={config.language as LexicalEditorProps['language']}
          options={getFilteredVariableList(selectedNode?.data?.type, key)}
          titleVariant={config.titleVariant}
          size="small"
        />
      </Form.Item>
    )
  }

  if (config.type === 'paramList') {
    return (
      <Form.Item key={key} name={key}>
        <ParamsList
          label={t(`workflow.config.${selectedNode?.data?.type}.${key}`)}
        />
      </Form.Item>
    )
  }
  if (config.type === 'groupVariableList') {
    return (
      <Form.Item key={key} name={key}>
        <GroupVariableList
          name={key}
          options={getFilteredVariableList(selectedNode?.data?.type, key)}
          isCanAdd={!!(values as any)?.group}
          size="small"
        />
      </Form.Item>
    )
  }
  if (config.type === 'caseList') {
    return (
      <Form.Item key={key} name={key} noStyle>
        <CaseList
          name={key}
          options={getFilteredVariableList(selectedNode?.data?.type, key)}
          selectedNode={selectedNode}
          graphRef={graphRef}
        />
      </Form.Item>
    )
  }
  if (config.type === 'cycleVarsList') {
    return (
      <Form.Item key={key} name={key}>
        <CycleVarsList
          size="small"
          parentName={key}
          options={getFilteredVariableList(selectedNode?.data?.type, key)}
          selectedNode={selectedNode}
          graphRef={graphRef}
        />
      </Form.Item>
    )
  }
  if (config.type === 'assignmentList') {
    return (
      <Form.Item key={key} name={key}>
        <AssignmentList
          parentName={key}
          options={(() => {
            if (config.filterLoopIterationVars) {
              const loopIterationVars: Suggestion[] = [];

              return [...getFilteredVariableList(selectedNode?.data?.type, key), ...loopIterationVars];
            }
            return getFilteredVariableList(selectedNode?.data?.type, key);
          })()
          }
        />
      </Form.Item>
    )
  }
  if (config.type === 'memoryConfig') {
    if (appType === 'pure_workflow') return null;
    return (
      <Form.Item
        key={key}
        name={key}
        noStyle
      >
        <MemoryConfig
          parentName={key}
          needMsg={config.needMsg as boolean}
          options={getFilteredVariableList('llm')}
        />
      </Form.Item>
    )
  }
  if (config.type === 'conditionList') {
    return (
      <Form.Item
        key={key}
        name={key}
        noStyle
      >
        <ConditionList
          parentName={key}
          options={(() => {
            const cycleVars = values?.cycle_vars || [];
            const cycleVarSuggestions: Suggestion[] = cycleVars.filter(vo => vo.name && vo.name.trim() !== '').map((cycleVar: any) => ({
              key: `${selectedNode.id}_cycle_${cycleVar.name}`,
              label: cycleVar.name,
              type: 'variable',
              dataType: cycleVar.type || 'string',
              value: `${selectedNode.getData().id}.${cycleVar.name}`,
              nodeData: selectedNode.getData(),
            }));

            return [...getFilteredVariableList(selectedNode?.data?.type, key), ...cycleVarSuggestions];
          })()}
          selectedNode={selectedNode}
          graphRef={graphRef}
          addBtnText={t('workflow.config.addCase')}
        />
      </Form.Item>
    )
  }
  if (config.type === 'mappingList') {
    return <MappingList
      key={key}
      label={t(`workflow.config.${selectedNode?.data?.type}.${key}`)}
      name={key}
      options={getFilteredVariableList(selectedNode?.data?.type, key)}
      isNeedType={config.isNeedType as boolean}
    />
  }
  if (config.type === 'errorHandle') {
    return (
      <Form.Item key={key} name={key}>
        <ErrorHandle
          selectedNode={selectedNode}
          graphRef={graphRef}
        />
      </Form.Item>
    )
  }

  if (key === 'vision_input' && !values?.vision) {
    return null
  }

  if (config.type === 'toolList') {
    return (
      <Form.Item
        key={key} name={key}
      >
        <ToolList />
      </Form.Item>
    )
  }

  return <BasicField configKey={key} config={config} />
}

export default ConfigField
