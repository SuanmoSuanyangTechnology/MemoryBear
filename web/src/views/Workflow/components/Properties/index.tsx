import { type FC, useEffect, useState, useRef, useMemo } from "react";
import { useTranslation } from 'react-i18next'
import { Graph, Node } from '@antv/x6';
import { Form, Input, Button, Select, InputNumber, Slider, Space, Divider, App, Switch } from 'antd'

import type { NodeConfig, NodeProperties, StartVariableItem, VariableEditModalRef } from '../../types'
import Empty from '@/components/Empty';
import emptyIcon from '@/assets/images/workflow/empty.png'
import CustomSelect from "@/components/CustomSelect";
import VariableEditModal from './VariableEditModal';
import MessageEditor from './MessageEditor'
import Knowledge from './Knowledge/Knowledge';
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'
import VariableSelect from './VariableSelect';
import ParamsList from './ParamsList';
import GroupVariableList from './GroupVariableList'
import CaseList from './CaseList'
import HttpRequest from './HttpRequest';
import MappingList from './MappingList'
import CategoryList from './CategoryList'
import ConditionList from './ConditionList'
import CycleVarsList from './CycleVarsList'
import AssignmentList from './AssignmentList'
import ToolConfig from './ToolConfig'

interface PropertiesProps {
  selectedNode?: Node | null; 
  setSelectedNode: (node: Node | null) => void;
  graphRef: React.MutableRefObject<Graph | undefined>;
  blankClick: () => void;
  deleteEvent: () => void;
  copyEvent: () => void;
  parseEvent: () => void;
  config?: any;
}
const Properties: FC<PropertiesProps> = ({
  selectedNode,
  graphRef,
  config,
}) => {
  const { t } = useTranslation()
  const { modal } = App.useApp()
  const [form] = Form.useForm<NodeConfig>();
  const [configs, setConfigs] = useState<Record<string,NodeConfig>>({} as Record<string,NodeConfig>)
  const values = Form.useWatch([], form);
  const variableModalRef = useRef<VariableEditModalRef>(null)
  const [editIndex, setEditIndex] = useState<number | null>(null)

  useEffect(() => {
    if (selectedNode?.getData()?.id) {
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
      const { id, knowledge_retrieval, group, group_names, ...rest } = values
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
  }, [values, selectedNode])

  const handleAddVariable = () => {
    variableModalRef.current?.handleOpen()
  }
  const handleEditVariable = (index: number, vo: StartVariableItem) => {
    variableModalRef.current?.handleOpen(vo)
    setEditIndex(index)
  }
  const handleRefreshVariable = (value: StartVariableItem) => {
    if (!selectedNode) return
    if (editIndex !== null) {
      const defaultValue = selectedNode.data.config.variables.defaultValue ?? []
      defaultValue[editIndex] = value
      selectedNode.data.config.variables.defaultValue = [...defaultValue]
    } else {
      const defaultValue = selectedNode.data.config.variables.defaultValue ?? []
      selectedNode.data.config.variables.defaultValue = [...defaultValue, value]
    }
    selectedNode?.setData({ ...selectedNode.data})

    setConfigs({ ...selectedNode.data.config})
  }
  const handleDeleteVariable = (index: number, vo: StartVariableItem) => {
    if (!selectedNode) return

    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: vo.name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        const defaultValue = selectedNode.data.config.variables.defaultValue ?? []
        defaultValue.splice(index, 1)
        selectedNode.data.config.variables.defaultValue = [...defaultValue]

        selectedNode?.setData({ ...selectedNode.data })

        setConfigs({ ...selectedNode.data.config })
      }
    })
  }

  const variableList = useMemo(() => {
    if (!selectedNode || !graphRef?.current) return [];
    
    const variableList: Suggestion[] = [];
    const graph = graphRef.current;
    const edges = graph.getEdges();
    const nodes = graph.getNodes();
    const addedKeys = new Set<string>();
    
    // Find all connected previous nodes (recursive)
    const getAllPreviousNodes = (nodeId: string, visited = new Set<string>()): string[] => {
      if (visited.has(nodeId)) return [];
      visited.add(nodeId);
      
      const directPrevious = edges
        .filter(edge => edge.getTargetCellId() === nodeId)
        .map(edge => edge.getSourceCellId());
      
      const allPrevious = [...directPrevious];
      directPrevious.forEach(prevNodeId => {
        allPrevious.push(...getAllPreviousNodes(prevNodeId, visited));
      });
      
      return allPrevious;
    };
    
    // Find child nodes (nodes whose cycle field equals current node's ID)
    const getChildNodes = (nodeId: string): string[] => {
      return nodes
        .filter(node => node.getData()?.cycle === nodeId)
        .map(node => node.id);
    };
    
    const allPreviousNodeIds = getAllPreviousNodes(selectedNode.id);
    const childNodeIds = getChildNodes(selectedNode.id);
    console.log('childNodeIds', selectedNode, childNodeIds)
    const allRelevantNodeIds = [...allPreviousNodeIds, ...childNodeIds];
    
    allRelevantNodeIds.forEach(nodeId => {
      const node = nodes.find(n => n.id === nodeId);
      if (!node) return;
      
      const nodeData = node.getData();

      switch(nodeData.type) {
        case 'start':
          const list = [
            ...(nodeData.config?.variables?.defaultValue ?? []),
            ...(nodeData.config?.variables?.value ?? [])
          ]
          list.forEach((variable: any) => {
            if (!variable || !variable?.name) return;
            const key = `${nodeId}_${variable.name}`;
            if (!addedKeys.has(key)) {
              addedKeys.add(key);
              variableList.push({
                key,
                label: variable.name,
                type: 'variable',
                dataType: variable.type,
                value: `${node.getData().id}.${variable.name}`,
                nodeData: nodeData,
              });
            }
          });
          nodeData.config?.variables?.sys?.forEach((variable: any) => {
            if (!variable || !variable?.name) return;
            const key = `${nodeId}_sys_${variable.name}`;
            if (!addedKeys.has(key)) {
              addedKeys.add(key);
              variableList.push({
                key,
                label: `sys.${variable.name}`,
                type: 'variable',
                dataType: variable.type,
                value: `sys.${variable.name}`,
                nodeData: nodeData,
              });
            }
          });
          break
        case 'llm':
          const llmKey = `${nodeId}_output`;
          if (!addedKeys.has(llmKey)) {
            addedKeys.add(llmKey);
            variableList.push({
              key: llmKey,
              label: 'output',
              type: 'variable',
              dataType: 'String',
              value: `${node.getData().id}.output`,
              nodeData: nodeData,
            });
          }
          break
        case 'knowledge-retrieval':
          const knowledgeKey = `${nodeId}_message`;
          if (!addedKeys.has(knowledgeKey)) {
            addedKeys.add(knowledgeKey);
            variableList.push({
              key: knowledgeKey,
              label: 'message',
              type: 'variable',
              dataType: 'array[object]',
              value: `${node.getData().id}.message`,
              nodeData: nodeData,
            });
          }
          break
        case 'parameter-extractor':
          const successKey = `${nodeId}___is_success`;
          const reasonKey = `${nodeId}___reason`;
          if (!addedKeys.has(successKey)) {
            addedKeys.add(successKey);
            variableList.push({
              key: successKey,
              label: '__is_success',
              type: 'variable',
              dataType: 'number',
              value: `${node.getData().id}.__is_success`,
              nodeData: nodeData,
            });
          }
          if (!addedKeys.has(reasonKey)) {
            addedKeys.add(reasonKey);
            variableList.push({
              key: reasonKey,
              label: '__reason',
              type: 'variable',
              dataType: 'string',
              value: `${node.getData().id}.__reason`,
              nodeData: nodeData,
            });
          }
          // Add params variables
          const paramsList = nodeData.config?.params?.defaultValue || [];
          paramsList.forEach((param: any) => {
            if (!param || !param?.name) return;
            const paramKey = `${nodeId}_${param.name}`;
            if (!addedKeys.has(paramKey)) {
              addedKeys.add(paramKey);
              variableList.push({
                key: paramKey,
                label: param.name,
                type: 'variable',
                dataType: param.type || 'string',
                value: `${node.getData().id}.${param.name}`,
                nodeData: nodeData,
              });
            }
          });
          break
        case 'var-aggregator':
          const varAggregatorKey = `${nodeId}_output`;
          if (!addedKeys.has(varAggregatorKey)) {
            addedKeys.add(varAggregatorKey);
            variableList.push({
              key: varAggregatorKey,
              label: 'output',
              type: 'variable',
              dataType: 'string',
              value: `${node.getData().id}.output`,
              nodeData: nodeData,
            });
          }
          break
        case 'http-request':
          const httpBodyKey = `${nodeId}_body`;
          const httpStatusKey = `${nodeId}_status_code`;
          if (!addedKeys.has(httpBodyKey)) {
            addedKeys.add(httpBodyKey);
            variableList.push({
              key: httpBodyKey,
              label: 'body',
              type: 'variable',
              dataType: 'string',
              value: `${node.getData().id}.body`,
              nodeData: nodeData,
            });
          }
          if (!addedKeys.has(httpStatusKey)) {
            addedKeys.add(httpStatusKey);
            variableList.push({
              key: httpStatusKey,
              label: 'status_code',
              type: 'variable',
              dataType: 'number',
              value: `${node.getData().id}.status_code`,
              nodeData: nodeData,
            });
          }
          break
        case 'jinja-render':
          const jinjaOutputKey = `${nodeId}_output`;
          if (!addedKeys.has(jinjaOutputKey)) {
            addedKeys.add(jinjaOutputKey);
            variableList.push({
              key: jinjaOutputKey,
              label: 'output',
              type: 'variable',
              dataType: 'string',
              value: `${node.getData().id}.output`,
              nodeData: nodeData,
            });
          }
          break
      }
    });

    // Add conversation variables from global config
    const conversationVariables = config?.variables || [];
    
    conversationVariables.forEach((variable: any) => {
      const key = `CONVERSATION_${variable.name}`;
      if (!addedKeys.has(key)) {
        addedKeys.add(key);
        variableList.push({
          key,
          label: variable.name,
          type: 'variable',
          dataType: variable.type,
          value: `conv.${variable.name}`,
          nodeData: { type: 'CONVERSATION', name: 'CONVERSATION', icon: '' },
          group: 'CONVERSATION'
        });
      }
    });

    return variableList;
  }, [selectedNode, graphRef]);

  console.log('values', values)
  console.log('variableList', variableList, selectedNode?.data)

  return (
    <div className="rb:w-75 rb:fixed rb:right-0 rb:top-16 rb:bottom-0 rb:p-3">
      <div className="rb:font-medium rb:leading-5 rb:mb-3">{t('workflow.nodeProperties')}</div>
      {!selectedNode
        ? <Empty url={emptyIcon} size={140} className="rb:h-full rb:mx-15" title={t('workflow.empty')} />
        : <Form form={form} layout="vertical" className="rb:h-[calc(100%-20px)] rb:overflow-y-auto">
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
                />
              : selectedNode?.data?.type === 'tool'
              ? <ToolConfig options={variableList} />
              : configs && Object.keys(configs).length > 0 && Object.keys(configs).map((key) => {
                const config = configs[key] || {}

                if (config.dependsOn && (values as any)?.[config.dependsOn as string] !== config.dependsOnValue) {
                  return null
                }

                if (selectedNode?.data?.type === 'start' && key === 'variables' && config.type === 'define') {
                  return (
                    <div key={key}>
                      <div className="rb:flex rb:items-center rb:justify-between rb:mb-2.75">
                        <div className="rb:leading-5">
                          {t(`workflow.config.${selectedNode?.data?.type}.${key}`)}
                        </div>
                        <Button style={{padding: '0 8px', height: '24px'}} onClick={handleAddVariable}>+{t('application.addVariables')}</Button>
                      </div>

                      <Space size={4} direction="vertical" className="rb:w-full">
                        {Array.isArray(config.defaultValue) && config.defaultValue?.map((vo, index) =>
                          <div key={`${vo.name}}-${index}`} className="rb:p-[4px_8px] rb:text-[12px] rb:text-[#5B6167] rb:flex rb:items-center rb:justify-between rb:border rb:border-[#DFE4ED] rb:rounded-md rb:group rb:cursor-pointer">
                            <span>{vo.name}·{vo.description}</span>

                            <div className="rb:group-hover:hidden rb:flex rb:items-center rb:gap-1">
                              {vo.required && <span>{t('workflow.config.start.required')}</span>}
                              {vo.type}
                            </div>
                            <Space className="rb:hidden! rb:group-hover:flex!">
                              <div
                                className="rb:w-4.5 rb:h-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]"
                                onClick={() => handleEditVariable(index, vo)}
                              ></div>
                              <div
                                className="rb:w-4.5 rb:h-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                                onClick={() => handleDeleteVariable(index, vo)}
                              ></div>
                            </Space>
                          </div>
                        )}
                        <Divider size="small" />
                        {config.sys?.map((vo, index) =>
                          <div key={index} className="rb:p-[4px_8px] rb:text-[12px] rb:text-[#5B6167] rb:flex rb:items-center rb:justify-between rb:border rb:border-[#DFE4ED] rb:rounded-md">
                            <div>
                              <span>sys.{vo.name}</span>
                            </div>
                            {vo.type}
                          </div>
                        )}
                      </Space>
                    </div>
                  )
                }

                if (selectedNode?.data?.type === 'llm' && key === 'messages' && config.type === 'define') {
                  // 为llm节点且isArray=true时添加context变量支持
                  let contextVariableList = [...variableList];
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
                      <MessageEditor options={contextVariableList} parentName={key} />
                    </Form.Item>
                  )
                }
                if (selectedNode?.data?.type === 'end' && key === 'output') {
                  return (
                    <Form.Item key={key} name={key}>
                      <MessageEditor isArray={false} parentName={key} options={variableList} />
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
                        options={variableList}
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
                        options={variableList}
                        isCanAdd={!!(values as any)?.group}
                      />
                    </Form.Item>
                  
                  )
                }
                if (config.type === 'caseList') {
                  return (
                    <Form.Item key={key} name={key}>
                      <CaseList
                        name={key}
                        options={variableList}
                        selectedNode={selectedNode}
                        graphRef={graphRef}
                      />
                    </Form.Item>
                  )
                }

                if (config.type === 'mappingList') {
                  return (
                    <Form.Item key={key} name={key}
                      label={t(`workflow.config.${selectedNode?.data?.type}.${key}`)}
                    >
                      <MappingList name={key} options={variableList} />
                    </Form.Item>
                  
                  )
                }
                if (config.type === 'cycleVarsList') {
                  return (
                    <Form.Item key={key} name={key}>
                      <CycleVarsList
                        parentName={key}
                        options={variableList}
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
                            // Add loop cycle variables and iteration item/index variables
                            const loopIterationVars: Suggestion[] = [];
                            const graph = graphRef.current;
                            if (graph && selectedNode) {
                              const nodes = graph.getNodes();
                              
                              // Find parent loop/iteration nodes
                              const findParentLoopIteration = (nodeId: string): string[] => {
                                const node = nodes.find(n => n.id === nodeId);
                                if (!node) return [];
                                
                                const nodeData = node.getData();
                                const cycle = nodeData?.cycle;
                                
                                if (cycle) {
                                  const parentNode = nodes.find(n => n.getData().id === cycle);
                                  if (parentNode) {
                                    const parentData = parentNode.getData();
                                    if (parentData?.type === 'loop') {
                                      console.log('parentData', parentData)
                                      // Add cycle variables from loop node
                                      const cycleVars = parentData.cycle_vars || [];
                                      cycleVars.forEach((cycleVar: any) => {
                                        loopIterationVars.push({
                                          key: `${cycle}_cycle_${cycleVar.name}`,
                                          label: cycleVar.name,
                                          type: 'variable',
                                          dataType: 'String',
                                          value: `${cycle}.${cycleVar.name}`,
                                          nodeData: parentData,
                                        });
                                      });
                                    } else if (parentData?.type === 'iteration') {
                                      // Add item and index variables from iteration node
                                      loopIterationVars.push(
                                        {
                                          key: `${cycle}_item`,
                                          label: 'item',
                                          type: 'variable',
                                          dataType: 'Object',
                                          value: `${cycle}.item`,
                                          nodeData: parentData,
                                        },
                                        {
                                          key: `${cycle}_index`,
                                          label: 'index',
                                          type: 'variable',
                                          dataType: 'Number',
                                          value: `${cycle}.index`,
                                          nodeData: parentData,
                                        }
                                      );
                                    }
                                    return [cycle, ...findParentLoopIteration(cycle)];
                                  }
                                }
                                return [];
                              };
                              
                              findParentLoopIteration(selectedNode.id);
                            }
                            
                            return [...variableList, ...loopIterationVars];
                          }
                          return variableList;
                        })()
                      }
                    />
                    </Form.Item>
                  )
                }

                return (
                  <Form.Item 
                    key={key} 
                    name={key} 
                    label={t(`workflow.config.${selectedNode?.data?.type}.${key}`)}
                    layout={config.type === 'switch' ? 'horizontal' : 'vertical'}
                  >
                    {config.type === 'input'
                      ? <Input placeholder={t('common.pleaseEnter')} />
                      : config.type === 'textarea'
                      ? <Input.TextArea placeholder={t('common.pleaseEnter')} />
                      : config.type === 'select'
                      ? <Select
                        options={config.needTranslation ? config.options?.map(vo => ({ ...vo, label: t(vo.label) })) : config.options}
                        placeholder={t('common.pleaseSelect')}
                      />
                      : config.type === 'inputNumber'
                      ? <InputNumber />
                      : config.type === 'slider'
                      ? <Slider min={config.min} max={config.max} step={config.step} />
                      : config.type === 'customSelect'
                      ? <CustomSelect
                        placeholder={t('common.pleaseSelect')}
                        url={config.url as string}
                        params={config.params}
                        hasAll={false}
                        valueKey={config.valueKey}
                        labelKey={config.labelKey}
                      />
                      : config.type === 'variableList'
                      ? <VariableSelect
                          placeholder={t('common.pleaseSelect')}
                          options={(() => {
                            // Apply filtering if specified in config
                            if (config.filterNodeTypes || config.filterVariableNames) {
                              return variableList.filter(variable => {
                                const nodeTypeMatch = !config.filterNodeTypes || 
                                  (Array.isArray(config.filterNodeTypes) && config.filterNodeTypes.includes(variable.nodeData?.type));
                                const variableNameMatch = !config.filterVariableNames || 
                                  (Array.isArray(config.filterVariableNames) && config.filterVariableNames.includes(variable.label));
                                return nodeTypeMatch && variableNameMatch;
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
                              
                              return variableList.filter(variable => 
                                childNodes.some(node => node.id === variable.nodeData?.id)
                              );
                            }
                            return variableList;
                          })()
                        }
                        />
                      : config.type === 'switch'
                      ? <Switch onChange={key === 'group' ? () => { form.setFieldValue('group_names', []) } : undefined} />
                      : config.type === 'categoryList'
                      ? <CategoryList parentName={key} selectedNode={selectedNode} graphRef={graphRef} />
                      : config.type === 'conditionList'
                      ? <ConditionList
                        parentName={key}
                        options={variableList}
                        selectedNode={selectedNode}
                        graphRef={graphRef}
                        addBtnText={t('workflow.config.addCase')}
                      />
                      : null
                    }
                  </Form.Item>
                )
              })
            }
          </Form>
      }

      <VariableEditModal
        ref={variableModalRef}
        refresh={handleRefreshVariable}
      />
    </div>
  );
};
export default Properties;