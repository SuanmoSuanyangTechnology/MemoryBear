/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:39:59 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 11:25:59
 */
import { type FC, useEffect, useState, useMemo } from "react";
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { Graph, Node } from '@antv/x6';
import { Form, Input, Select, InputNumber, Switch, Flex, Space, Dropdown, type MenuProps, Button } from 'antd';

import type { NodeConfig, NodeProperties, ChatVariable } from '../../types'
import CustomSelect from "@/components/CustomSelect";
import MessageEditor from './MessageEditor'
import Knowledge from './Knowledge/Knowledge';
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
import VariableSelect from './VariableSelect';
import ParamsList from './ParamsList';
import GroupVariableList from './GroupVariableList'
import CaseList from './CaseList'
import HttpRequest from './HttpRequest';
import CategoryList from './CategoryList'
import ConditionList from './ConditionList'
import CycleVarsList from './CycleVarsList'
import AssignmentList from './AssignmentList'
import ToolConfig from './ToolConfig'
import MemoryConfig from './MemoryConfig'
import VariableList from './VariableList'
import { useVariableList, getCurrentNodeVariables, getChildNodeVariables } from './hooks/useVariableList'
import styles from './properties.module.css'
import Editor, { type LexicalEditorProps } from "../Editor";
import RbSlider from '@/components/RbSlider'
import JinjaRender from './JinjaRender'
import CodeExecution from './CodeExecution'
import { nodeLibrary } from '../../constant';
import RbCard from '@/components/RbCard/Card';
import ModelConfig from './ModelConfig'
import ModelSelect from '@/components/ModelSelect'

/**
 * Props for Properties component
 */
interface PropertiesProps {
  /** Currently selected node */
  selectedNode: Node;
  /** Reference to graph instance */
  graphRef: React.MutableRefObject<Graph | undefined>;
  /** Handler for blank canvas click */
  blankClick: () => void;
  /** Handler for delete event */
  deleteEvent: () => void;
  /** Handler for copy event */
  copyEvent: () => void;
  /** Handler for paste event */
  parseEvent: () => void;
  /** Workflow configuration */
  config?: any;
  /** Chat variables */
  chatVariables: ChatVariable[];
}

/**
 * Properties panel component
 * Displays and manages configuration for selected workflow node
 * @param props - Component props
 */
const Properties: FC<PropertiesProps> = ({
  selectedNode,
  graphRef,
  chatVariables,
  blankClick
}) => {
  const { t } = useTranslation()
  const [form] = Form.useForm<NodeConfig>();
  const [configs, setConfigs] = useState<Record<string, NodeConfig>>({} as Record<string, NodeConfig>)
  const values = Form.useWatch([], form);
  const variableList = useVariableList(selectedNode, graphRef, chatVariables)

  useEffect(() => {
    if (selectedNode?.getData()?.id) {
      setOutputCollapsed(true)
    }
    form.resetFields()
  }, [selectedNode?.getData()?.id])

  useEffect(() => {
    if (selectedNode && form) {
      const { type = 'default', name = '', config, id } = selectedNode.getData() || {}
      const initialValue: Record<string, any> = {}
      Object.keys(config || {}).forEach(key => {
        if (config && config[key] && 'defaultValue' in config[key]) {
          initialValue[key] = config[key].defaultValue
        }
      })

      form.setFieldsValue({
        type,
        id,
        name,
        ...initialValue,
      })
      setConfigs(config || {})
    } else {
      form.resetFields()
    }
  }, [selectedNode, form])

  /**
   * Update node label in graph
   * @param newLabel - New label text
   */
  const updateNodeLabel = (newLabel: string) => {
    if (selectedNode && form) {
      const nodeData = selectedNode.getData() as NodeProperties;
      selectedNode.setAttrByPath('text/text', `${nodeData.icon} ${newLabel}`);
      selectedNode.setData({ ...selectedNode.getData(), name: newLabel });
    }
  };

  useEffect(() => {
    if (values && selectedNode) {
      const { id, knowledge_retrieval, group, group_variables, ...rest } = values
      const { knowledge_bases = [], name: _name, description: _description, ...restKnowledgeConfig } = (knowledge_retrieval as any) || {}

      let allRest = {
        ...rest,
        ...restKnowledgeConfig,
      }
      if (knowledge_bases?.length) {
        allRest.knowledge_bases = knowledge_bases?.map((vo: any) => ({
          id: vo.id,
          ...vo.config
        }))
      }

      const nodeData = selectedNode.getData()

      Object.keys(values).forEach(key => {
        if (nodeData?.config?.[key]) {
          // Create a deep copy to avoid reference sharing between nodes
          if (!nodeData.config[key]) {
            nodeData.config[key] = {};
          }
          nodeData.config[key] = {
            ...nodeData.config[key],
            defaultValue: values[key]
          };
        }
      })

      selectedNode?.setData({
        ...nodeData,
        ...allRest,
      })
    }
  }, [values, selectedNode, form])



  /**
   * Get filtered variable list based on node type and config key
   * @param nodeType - Type of the node
   * @param key - Configuration key
   * @returns Filtered variable list
   */
  const getFilteredVariableList = (nodeType?: string, key?: string) => {
    // Check if current node is a child of iteration node
    const parentIterationNode = selectedNode ? (() => {
      const nodes = graphRef.current?.getNodes() || [];
      const nodeData = selectedNode.getData();
      const cycle = nodeData?.cycle;

      if (cycle) {
        const parentNode = nodes.find(n => n.getData().id === cycle);
        if (parentNode) {
          const parentData = parentNode.getData();
          if (parentData?.type === 'iteration') {
            return parentNode;
          }
        }
      }
      return null;
    })() : null;

    // Helper function to add parent iteration variables
    const addParentIterationVars = (filteredList: any[]) => {
      if (parentIterationNode) {
        const parentData = parentIterationNode.getData();
        const parentNodeId = parentData.id;

        if (parentData.config?.input?.defaultValue) {
          const itemKey = `${parentNodeId}_item`;
          const indexKey = `${parentNodeId}_index`;

          const existingItemVar = filteredList.find(v => v.key === itemKey);
          const existingIndexVar = filteredList.find(v => v.key === indexKey);

          if (!existingItemVar) {
            // Determine item dataType from input variable
            let itemDataType = 'object';
            const inputVariable = variableList.find(v => `{{${v.value}}}` === parentData.config.input.defaultValue);
            if (inputVariable && inputVariable.dataType.startsWith('array[')) {
              itemDataType = inputVariable.dataType.replace(/^array\[(.+)\]$/, '$1');
            }

            filteredList.push({
              key: itemKey,
              label: 'item',
              type: 'variable',
              dataType: itemDataType,
              value: `${parentNodeId}.item`,
              nodeData: parentData,
            });
          }

          if (!existingIndexVar) {
            filteredList.push({
              key: indexKey,
              label: 'index',
              type: 'variable',
              dataType: 'number',
              value: `${parentNodeId}.index`,
              nodeData: parentData,
            });
          }
        }
      }
      return filteredList;
    };

    if (nodeType === 'llm') {
      // For LLM nodes that are children of iteration or loop nodes, include parent variables
      const parentLoopNode = selectedNode ? (() => {
        const nodes = graphRef.current?.getNodes() || [];
        const nodeData = selectedNode.getData();
        const cycle = nodeData?.cycle;

        if (cycle) {
          const parentNode = nodes.find(n => n.getData().id === cycle);
          if (parentNode) {
            const parentData = parentNode.getData();
            if (parentData?.type === 'loop' || parentData?.type === 'iteration') {
              return parentNode;
            }
          }
        }
        return null;
      })() : null;

      let filteredList = variableList.filter(variable => variable.dataType !== 'boolean');

      // If this LLM node is a child of iteration/loop, ensure parent variables are included
      if (parentLoopNode) {
        const parentData = parentLoopNode.getData();
        const parentNodeId = parentData.id;

        // Ensure parent loop/iteration variables are included
        if (parentData.type === 'loop') {
          const cycleVars = parentData.cycle_vars || [];
          cycleVars.forEach((cycleVar: any) => {
            const key = `${parentNodeId}_cycle_${cycleVar.name}`;
            const existingVar = filteredList.find(v => v.key === key);
            if (!existingVar && cycleVar.name && cycleVar.type !== 'boolean') {
              filteredList.push({
                key,
                label: cycleVar.name,
                type: 'variable',
                dataType: cycleVar.type || 'String',
                value: `${parentNodeId}.${cycleVar.name}`,
                nodeData: parentData,
              });
            }
          });
        } else if (parentData.type === 'iteration') {
          // Add item and index variables for iteration parent
          if (parentData.config?.input?.defaultValue) {
            const itemKey = `${parentNodeId}_item`;
            const indexKey = `${parentNodeId}_index`;

            const existingItemVar = filteredList.find(v => v.key === itemKey);
            const existingIndexVar = filteredList.find(v => v.key === indexKey);

            if (!existingItemVar) {
              // Determine item dataType from input variable
              let itemDataType = 'object';
              const inputVariable = variableList.find(v => `{{${v.value}}}` === parentData.config.input.defaultValue);
              if (inputVariable && inputVariable.dataType.startsWith('array[')) {
                itemDataType = inputVariable.dataType.replace(/^array\[(.+)\]$/, '$1');
              }

              filteredList.push({
                key: itemKey,
                label: 'item',
                type: 'variable',
                dataType: itemDataType,
                value: `${parentNodeId}.item`,
                nodeData: parentData,
              });
            }

            if (!existingIndexVar) {
              filteredList.push({
                key: indexKey,
                label: 'index',
                type: 'variable',
                dataType: 'Number',
                value: `${parentNodeId}.index`,
                nodeData: parentData,
              });
            }
          }
        }
      }

      return filteredList;
    }
    if (nodeType === 'knowledge-retrieval' || nodeType === 'parameter-extractor' && key !== 'prompt' || nodeType === 'memory-read' || nodeType === 'memory-write' || nodeType === 'question-classifier') {
      let filteredList = addParentIterationVars(variableList).filter(variable => variable.dataType === 'string');
      return filteredList;
    }
    if (nodeType === 'parameter-extractor' && key === 'prompt') {
      let filteredList = addParentIterationVars(variableList).filter(variable => variable.dataType === 'string' || variable.dataType === 'number');
      return filteredList;
    }
    if (nodeType === 'iteration' && key === 'output' || nodeType === 'loop' && key === 'condition') {
      if (!selectedNode) return [];
      let filteredList = nodeType === 'iteration'
        ? variableList.filter(variable => variable.value.includes('sys.'))
        : addParentIterationVars(variableList).filter(variable => variable.nodeData.type !== 'loop');

      const childVariables = getChildNodeVariables(selectedNode, graphRef);
      const existingKeys = new Set(filteredList.map(v => v.key));
      childVariables.forEach(v => {
        if (!existingKeys.has(v.key)) {
          filteredList.push(v);
          existingKeys.add(v.key);
        }
      });

      return filteredList;
    }
    if (nodeType === 'iteration') {
      return variableList.filter(variable => variable.dataType.includes('array'));
    }

    // For all other node types, add parent iteration variables if applicable
    let baseList = variableList;
    return addParentIterationVars(baseList);
  };

  // const defaultVariableList = calculateVariableList(selectedNode as Node, graphRef, workflowConfig )

  console.log('values', values)

  /**
   * Get current node output variables
   */
  const currentNodeVariables = useMemo(() => {
    if (!selectedNode) return []
    return getCurrentNodeVariables(selectedNode?.getData(), values)
  }, [selectedNode?.getData(), values])

  const [outputCollapsed, setOutputCollapsed] = useState(true)
  /**
   * Toggle output section collapsed state
   */
  const handleToggle = () => {
    setOutputCollapsed((prev: boolean) => !prev)
  }

  /**
   * Handle variable list change and update output type for iteration nodes
   * @param _value - Selected value
   * @param option - Selected option
   * @param key - Configuration key
   */
  const handleChangeVariableList = (_value: string, option: any, key: string) => {
    if (selectedNode?.data?.type === 'iteration' && key === 'output') {
      form.setFieldValue('output_type', option?.dataType)
    }
  }
  console.log('variableList', variableList, currentNodeVariables)
  const handleSureReplace = () => {
    const { replaceNode } = values;
    const nodeLibraryConfig = [...nodeLibrary]
      .flatMap(category => category.nodes)
      .find(n => n.type === replaceNode)

    if (replaceNode && nodeLibraryConfig) {
      // Preserve existing config values when switching node types
      const currentData = selectedNode?.data || {};
      const currentConfig = currentData.config || {};
      const newConfig = nodeLibraryConfig.config || {};

      // Merge configs: keep existing values for matching keys, add new keys from template
      const mergedConfig: Record<string, any> = {};
      Object.keys(newConfig).forEach(key => {
        if (currentConfig[key] && currentConfig[key].defaultValue !== undefined) {
          // Preserve existing value if it exists
          mergedConfig[key] = {
            ...newConfig[key],
            defaultValue: currentConfig[key].defaultValue
          };
        } else {
          // Use new config template
          mergedConfig[key] = { ...newConfig[key] };
        }
      });

      selectedNode?.setData({
        ...currentData,
        ...nodeLibraryConfig,
        config: mergedConfig
      })
      blankClick()
    }
  }
  const handleClick: MenuProps['onClick'] = (e) => {
    switch (e.key) {
      case 'delete':
        selectedNode.remove()
        break;
      case 'copy':
        break;
    }
  }

  return (
    <div className={clsx("rb:w-90 rb:fixed rb:right-2.5 rb:top-18.5 rb:bottom-2.5 rb:z-1000", styles.properties)}>
      <RbCard
        title={t('workflow.nodeProperties')}
        extra={<Space>
          <Dropdown
            menu={{
              items: [
                { key: 'delete', icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/delete_dark.svg')]"></div>, label: <Flex>{t('common.delete')}</Flex> },
                // { key: 'copy', icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/copy_dark.svg')]"></div>, label: t('common.copy') }
              ],
              onClick: handleClick
            }}
          >
            <div className="rb:cursor-pointer rb:size-4 rb:hover:bg-[#F6F6F6] rb:rounded-sm rb:bg-cover rb:bg-[url(@/assets/images/common/dash.svg)]">
            </div>
          </Dropdown>
          <div className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/close.svg')]" onClick={blankClick}></div>
        </Space>}
        headerType="borderless"
        headerClassName={clsx("rb:font-[MiSans-Bold] rb:font-bold rb:min-h-[48px]!")}
        className="rb:h-full! rb:hover:shadow-none!"
        bodyClassName={clsx('rb:overflow-y-auto! rb:h-[calc(100vh-131px)]! rb:px-3! rb:pt-0! rb:pb-3!')}
      >
        <Form form={form} size="small" layout="vertical">
          <Form.Item name="name" label={t('workflow.nodeName')}>
            <Input
              placeholder={t('common.pleaseEnter')}
              onChange={(e) => {
                updateNodeLabel(e.target.value);
              }}
            />
          </Form.Item>
          <Form.Item name="id" label="ID">
            <Input disabled />
          </Form.Item>
          {selectedNode?.data?.type === 'unknown'
            ? <>
              <Form.Item name="replaceNode" label={t('workflow.config.unknown.replaceNodeType')}>
                <Select
                  options={nodeLibrary.map(category => ({
                    label: t(`workflow.${category.category}`),
                    options: category.nodes.filter(item => !['cycle-start', 'break'].includes(item.type)).map(node => ({
                      label: <div className="rb:flex rb:items-center rb:gap-2 rb:flex-1">
                        <img src={node.icon} className="rb:size-3.5" />
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
                    options={getFilteredVariableList(selectedNode?.data?.type, 'mapping')}
                    templateOptions={getFilteredVariableList(selectedNode?.data?.type, 'template')}
                  />
                  : selectedNode?.data?.type === 'code'
                    ? <CodeExecution
                      selectedNode={selectedNode}
                      options={getFilteredVariableList(selectedNode?.data?.type, 'mapping')}
                    />
                    : configs && Object.keys(configs).length > 0 && Object.keys(configs).map((key) => {
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
                              config={config}
                            />
                          </Form.Item>
                        )
                      }

                      if (key === 'model_id' && selectedNode?.data?.type === 'llm') {
                        return <ModelConfig />
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
                              dataType: 'String',
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
                      if (config.type === 'define') {
                        return null
                      }

                      if (config.type === 'knowledge') {
                        return (
                          <Form.Item
                            key={key}
                            name={key}
                          >
                            <Knowledge />
                          </Form.Item>
                        )
                      }

                      if (config.type === 'messageEditor') {
                        return (
                          <Form.Item key={key} name={key} label={selectedNode?.data?.type === 'memory-write' ? t(`workflow.config.${selectedNode?.data?.type}.${key}`) : undefined}>
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
                        return (
                          <Form.Item
                            key={key}
                            name={key}
                            noStyle
                          >
                            <MemoryConfig
                              parentName={key}
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
                                  dataType: cycleVar.type || 'String',
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

                      if (key === 'vision_input' && !values?.vision) {
                        return null
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
                                        : config.type === 'variableList'
                                          ? <VariableSelect
                                            placeholder={t(config.placeholder || 'common.pleaseSelect')}
                                            options={(() => {
                                              const baseVariableList = getFilteredVariableList(selectedNode?.data?.type, key);
                                              // Apply filtering if specified in config
                                              if (config.filterNodeTypes || config.filterVariableNames) {
                                                return baseVariableList.filter(variable => {
                                                  const nodeTypeMatch = !config.filterNodeTypes ||
                                                    (Array.isArray(config.filterNodeTypes) && config.filterNodeTypes.includes(variable.nodeData?.type));
                                                  const variableNameMatch = !config.filterVariableNames ||
                                                    (Array.isArray(config.filterVariableNames) && config.filterVariableNames.includes(variable.label));
                                                  return nodeTypeMatch || variableNameMatch;
                                                });
                                              }
                                              if (config.onFilterVariableNames) {
                                                return baseVariableList.filter(variable => Array.isArray(config.onFilterVariableNames) && config.onFilterVariableNames.includes(variable.label));
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
                                            onChange={(value, option) => handleChangeVariableList(value, option, key)}
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
                                                ? <Editor options={variableList} variant="outlined" size="small" placeholder={config.placeholder || t('common.pleaseEnter')} />
                                                : null
                          }
                        </Form.Item>
                      )
                    })
          }
        </Form>

        {currentNodeVariables.length > 0 && !(!values?.group && selectedNode.getData().type === 'var-aggregator') &&
          <div className="rb:text-[12px] rb:leading-4.5">
            <Flex gap={8} vertical>
              <Flex align="center" className="rb:font-medium rb:cursor-pointer" onClick={handleToggle}>
                {t('workflow.config.output')}
                <div
                  className={clsx("rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/caret_right_outlined.svg')]", {
                    'rb:rotate-90': !outputCollapsed
                  })}
                ></div>
              </Flex>
              {!outputCollapsed && currentNodeVariables.map(vo => (
                <Flex key={vo.value} gap={4}>
                  <span className="rb:font-medium">{vo.label}</span>
                  <span className="rb:text-[#212332]">{vo.dataType}</span>
                </Flex>
              ))}
            </Flex>
          </div>
        }
      </RbCard>
    </div>
  );
};
export default Properties;