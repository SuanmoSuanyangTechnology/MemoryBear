import { type FC, useEffect, useState, useMemo } from "react";
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { Graph, Node } from '@antv/x6';
import { Form, Input, Select, InputNumber, Switch, Divider, Space } from 'antd'
import { CaretDownOutlined, CaretRightOutlined } from '@ant-design/icons';

import type { NodeConfig, NodeProperties, ChatVariable } from '../../types'
import Empty from '@/components/Empty';
import emptyIcon from '@/assets/images/workflow/empty.png'
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
import { useVariableList, getCurrentNodeVariables } from './hooks/useVariableList'
import styles from './properties.module.css'
import Editor from "../Editor";
import RbSlider from './RbSlider'
import JinjaRender from './JinjaRender'

interface PropertiesProps {
  selectedNode?: Node | null; 
  setSelectedNode: (node: Node | null) => void;
  graphRef: React.MutableRefObject<Graph | undefined>;
  blankClick: () => void;
  deleteEvent: () => void;
  copyEvent: () => void;
  parseEvent: () => void;
  config?: any;
  chatVariables: ChatVariable[];
}
const Properties: FC<PropertiesProps> = ({
  selectedNode,
  graphRef,
  chatVariables
}) => {
  const { t } = useTranslation()
  const [form] = Form.useForm<NodeConfig>();
  const [configs, setConfigs] = useState<Record<string,NodeConfig>>({} as Record<string,NodeConfig>)
  const values = Form.useWatch([], form);
  const variableList = useVariableList(selectedNode, graphRef, chatVariables)

  useEffect(() => {
    if (selectedNode?.getData()?.id) {
      setOutputCollapsed(true)
    } else {
      form.resetFields()
    }
  }, [selectedNode?.getData()?.id])

  useEffect(() => {
    if (selectedNode && form) {
      const { type = 'default', name = '', config } = selectedNode.getData() || {}
      const initialValue: Record<string, any> = {}
      Object.keys(config || {}).forEach(key => {
        if (config && config[key] && 'defaultValue' in config[key]) {
          initialValue[key] = config[key].defaultValue
        }
      })

      form.setFieldsValue({
        type,
        id: selectedNode.id,
        name,
        ...initialValue,
      })
      setConfigs(config || {})
    } else {
      form.resetFields()
    }
  }, [selectedNode, form])

  const updateNodeLabel = (newLabel: string) => {
    if (selectedNode && form) {
      const nodeData = selectedNode.data as NodeProperties;
      selectedNode.setAttrByPath('text/text', `${nodeData.icon} ${newLabel}`);
      selectedNode.setData({ ...selectedNode.data, name: newLabel });
    }
  };

  useEffect(() => {
    if (values && selectedNode) {
      const { id, knowledge_retrieval, group, group_variables, ...rest } = values
      const { knowledge_bases = [], ...restKnowledgeConfig } = (knowledge_retrieval as any) || {}

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



      Object.keys(values).forEach(key => {
        if (selectedNode.data?.config?.[key]) {
          // Create a deep copy to avoid reference sharing between nodes
          if (!selectedNode.data.config[key]) {
            selectedNode.data.config[key] = {};
          }
          selectedNode.data.config[key] = {
            ...selectedNode.data.config[key],
            defaultValue: values[key]
          };
        }
      })

      selectedNode?.setData({
        ...selectedNode.data,
        ...allRest,
      })
    }
  }, [values, selectedNode, form])



  // Filter out boolean type variables for loop and llm nodes
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
    if (nodeType === 'iteration' && key === 'output') {
      return variableList.filter(variable => variable.value.includes('sys.'));
    }
    if (nodeType === 'iteration') {
      return variableList.filter(variable => variable.dataType.includes('array'));
    }
    if (nodeType === 'loop' && key === 'condition') {
      let filteredList = addParentIterationVars(variableList).filter(variable => variable.nodeData.type !== 'loop');
      
      // Add child node output variables for loop nodes
      if (selectedNode) {
        const graph = graphRef.current;
        if (graph) {
          const nodes = graph.getNodes();
          const childNodes = nodes.filter(node => {
            const nodeData = node.getData();
            return nodeData?.cycle === selectedNode.id;
          });
          
          // Add output variables from child nodes
          childNodes.forEach(childNode => {
            const childData = childNode.getData();
            const childNodeId = childData.id;
            
            // Add child node output variables based on their type
            switch(childData.type) {
              case 'llm':
              case 'jinja-render':
              case 'tool':
                const outputKey = `${childNodeId}_output`;
                const existingOutput = filteredList.find(v => v.key === outputKey);
                if (!existingOutput) {
                  filteredList.push({
                    key: outputKey,
                    label: 'output',
                    type: 'variable',
                    dataType: 'string',
                    value: `${childNodeId}.output`,
                    nodeData: childData,
                  });
                }
                break;
              case 'http-request':
                const bodyKey = `${childNodeId}_body`;
                const statusKey = `${childNodeId}_status_code`;
                if (!filteredList.find(v => v.key === bodyKey)) {
                  filteredList.push({
                    key: bodyKey,
                    label: 'body',
                    type: 'variable',
                    dataType: 'string',
                    value: `${childNodeId}.body`,
                    nodeData: childData,
                  });
                }
                if (!filteredList.find(v => v.key === statusKey)) {
                  filteredList.push({
                    key: statusKey,
                    label: 'status_code',
                    type: 'variable',
                    dataType: 'number',
                    value: `${childNodeId}.status_code`,
                    nodeData: childData,
                  });
                }
                break;
            }
          });
        }
      }
      
      return filteredList;
    }
    
    // For all other node types, add parent iteration variables if applicable
    let baseList = variableList;
    return addParentIterationVars(baseList);
  };

  // const defaultVariableList = calculateVariableList(selectedNode as Node, graphRef, workflowConfig )

  console.log('values', values)

  const currentNodeVariables = useMemo(() => {
    if (!selectedNode) return []
    return getCurrentNodeVariables(selectedNode?.getData(), values)
  }, [selectedNode?.getData(), values])

  const [outputCollapsed, setOutputCollapsed] = useState(true)
  const handleToggle = () => {
    setOutputCollapsed((prev: boolean) => !prev)
  }
  console.log('variableList', variableList, currentNodeVariables)

  return (
    <div className={clsx("rb:w-75 rb:fixed rb:right-0 rb:top-16 rb:bottom-0 rb:p-3 rb:pb-6", styles.properties)}>
      <div className="rb:font-medium rb:leading-5 rb:pb-3 rb:mb-3 rb:border-b rb:border-b-[#DFE4ED]">{t('workflow.nodeProperties')}</div>
      {!selectedNode
        ? <Empty url={emptyIcon} size={140} className="rb:h-full rb:mx-15" title={t('workflow.empty')} />
        : <div className="rb:h-[calc(100%-20px)] rb:overflow-x-hidden rb:overflow-y-auto">
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
          
          {selectedNode?.data?.type === 'http-request'
            ? <HttpRequest 
                options={variableList} 
                selectedNode={selectedNode}
                graphRef={graphRef}
              />
            : selectedNode?.data?.type === 'tool'
            ? <ToolConfig options={variableList} />
            : selectedNode?.data.type === 'jinja-render'
            ? <JinjaRender
              selectedNode={selectedNode}
              options={getFilteredVariableList(selectedNode?.data?.type, 'mapping')}
              templateOptions={getFilteredVariableList(selectedNode?.data?.type, 'template')}
            />
            : configs && Object.keys(configs).length > 0 && Object.keys(configs).map((key) => {
              const config = configs[key] || {}

              if (config.dependsOn && (values as any)?.[config.dependsOn as string] !== config.dependsOnValue) {
                return null
              }

              if (selectedNode?.data?.type === 'start' && key === 'variables' && config.type === 'define') {
                return (
                  <Form.Item key={key} name={key}>
                    <VariableList
                      parentName={key}
                      selectedNode={selectedNode}
                      config={config}
                    />
                  </Form.Item>
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
                  <Form.Item key={key} name={key}>
                    <MessageEditor 
                      title={t(`workflow.config.${selectedNode?.data?.type}.${key}`)}
                      isArray={!!config.isArray} 
                      parentName={key}
                      enableJinja2={config.enableJinja2 as boolean}
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
                  <Form.Item key={key} name={key}>
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

              return (
                <Form.Item 
                  key={key} 
                  name={key}
                  label={key === 'parallel_count' ? <span className="rb:text-[10px] rb:text-[#5B6167] rb:leading-3.5 rb:-mb-1!">{t(`workflow.config.${selectedNode?.data?.type}.${key}`)}</span> : t(`workflow.config.${selectedNode?.data?.type}.${key}`)}
                  layout={config.type === 'switch' ? 'horizontal' : 'vertical'}
                  className={key === 'parallel_count' ? 'rb:-mt-3! rb:leading-3.5!' : ''}
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
                    ? <RbSlider min={config.min} max={config.max} step={config.step} />
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
                      })()
                      }
                      size="small"
                    />
                    : config.type === 'switch'
                    ? <Switch onChange={key === 'group' ? () => { form.setFieldValue('group_variables', []) } : undefined} />
                    : config.type === 'categoryList'
                    ? <CategoryList 
                      parentName={key} 
                      selectedNode={selectedNode}
                      graphRef={graphRef}
                      options={getFilteredVariableList(selectedNode?.data?.type, key)}
                    />
                    : config.type === 'editor'
                    ? <Editor options={variableList} variant="outlined" size="small" />
                    : null
                  }
                </Form.Item>
              )
            })
          }
        </Form>

        {currentNodeVariables.length > 0 && !(!values?.group && selectedNode.getData().type === 'var-aggregator') &&
          <div className="rb:pb-3">
            <Divider />
            <Space size={8} direction="vertical" className="rb:max-w-full!">
              <div className="rb:font-medium rb:text-[12px] rb:leading-4.5 rb:cursor-pointer rb:ml-4" onClick={handleToggle}>
                {t('workflow.config.output')}
                {outputCollapsed ? <CaretRightOutlined /> : <CaretDownOutlined />}
              </div>
              {!outputCollapsed && currentNodeVariables.map(vo => (
                <div key={vo.value} className="rb:ml-4 rb:text-[12px] rb:flex rb:gap-2">
                  <span className="rb:font-medium">{vo.label}</span>
                  <span className="rb:text-[#5B6167]">{vo.dataType}</span>
                </div>
              ))}
            </Space>
          </div>
        }
      </div>}
    </div>
  );
};
export default Properties;