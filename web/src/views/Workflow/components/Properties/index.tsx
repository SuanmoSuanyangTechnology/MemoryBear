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
  config: workflowConfig,
}) => {
  const { t } = useTranslation()
  const { modal } = App.useApp()
  const [form] = Form.useForm<NodeConfig>();
  const [configs, setConfigs] = useState<Record<string,NodeConfig>>({} as Record<string,NodeConfig>)
  const values = Form.useWatch([], form);
  const variableModalRef = useRef<VariableEditModalRef>(null)
  const [editIndex, setEditIndex] = useState<number | null>(null)
  const prevMappingNamesRef = useRef<string[]>([])
  const prevTemplateVarsRef = useRef<string[]>([])
  const syncTimeoutRef = useRef<number | null>(null)
  const isSyncingRef = useRef(false)
  const lastSyncSourceRef = useRef<'mapping' | 'template' | null>(null)

  useEffect(() => {
    if (selectedNode?.getData()?.id) {
      form.resetFields()
      prevMappingNamesRef.current = []
      prevTemplateVarsRef.current = []
      lastSyncSourceRef.current = null
    }
  }, [selectedNode?.getData()?.id])

  // Sync template when mapping names change
  useEffect(() => {
    if (isSyncingRef.current || lastSyncSourceRef.current === 'mapping' || selectedNode?.data?.type !== 'jinja-render' || !values?.mapping || !values?.template) return
    
    const currentMappingNames = Array.isArray(values.mapping) ? values.mapping.filter(item => item && item.name).map((item: any) => item.name) : []
    const prevNames = prevMappingNamesRef.current
    
    if (prevNames.length === 0) {
      prevMappingNamesRef.current = currentMappingNames
      return
    }
    
    if (JSON.stringify(prevNames) === JSON.stringify(currentMappingNames)) return
    
    if (syncTimeoutRef.current) clearTimeout(syncTimeoutRef.current)
    const activeElement = document.activeElement as HTMLElement
    
    syncTimeoutRef.current = setTimeout(() => {
      let updatedTemplate = String(form.getFieldValue('template') || '')
      
      prevNames.forEach((oldName, index) => {
        const newName = currentMappingNames[index]
        if (newName && oldName !== newName) {
          updatedTemplate = updatedTemplate.replace(new RegExp(`{{\\s*${oldName}\\s*}}`, 'g'), `{{${newName}}}`)
        }
      })
      
      if (updatedTemplate !== form.getFieldValue('template')) {
        isSyncingRef.current = true
        lastSyncSourceRef.current = 'mapping'
        const newTemplateVars = (updatedTemplate.match(/{{\s*([\w.]+)\s*}}/g) || []).map(m => m.replace(/{{\s*|\s*}}/g, ''))
        prevTemplateVarsRef.current = newTemplateVars
        prevMappingNamesRef.current = currentMappingNames
        form.setFieldValue('template', updatedTemplate)
        
        requestAnimationFrame(() => {
          activeElement?.focus?.()
          setTimeout(() => { 
            isSyncingRef.current = false
            lastSyncSourceRef.current = null
          }, 50)
        })
      } else {
        prevMappingNamesRef.current = currentMappingNames
      }
    }, 0)
  }, [values?.mapping, selectedNode?.data?.type, form])

  // Sync mapping when template variables change
  useEffect(() => {
    if (isSyncingRef.current || lastSyncSourceRef.current === 'template' || selectedNode?.data?.type !== 'jinja-render' || !values?.template || !values?.mapping) return
    
    const templateVars = (String(values.template).match(/{{\s*([\w.]+)\s*}}/g) || []).map(m => m.replace(/{{\s*|\s*}}/g, ''))
    if (JSON.stringify(prevTemplateVarsRef.current) === JSON.stringify(templateVars)) return
    
    const isTemplateEditor = document.activeElement?.closest('[data-editor-type="template"]')
    if (!isTemplateEditor) {
      prevTemplateVarsRef.current = templateVars
      return
    }
    
    const updatedMapping = Array.isArray(values.mapping) ? [...values.mapping.filter(item => item)] : []
    const existingNames = updatedMapping.filter(item => item && item.name).map(item => item.name)
    let updatedTemplate = String(values.template)
    
    if (prevTemplateVarsRef.current.length > 0) {
      prevTemplateVarsRef.current.forEach((oldVar, index) => {
        const newVar = templateVars[index]
        if (newVar && oldVar !== newVar && updatedMapping[index]) {
          updatedMapping[index] = { ...updatedMapping[index], name: newVar }
        }
      })
    }
    
    templateVars.forEach(varName => {
      const existingMapping = updatedMapping.find(item => item.value === `{{${varName}}}`)
      const regex = new RegExp(`{{\\s*${varName.replace(/\./g, '\\.')}\\s*}}`, 'g')
      
      if (existingMapping) {
        updatedTemplate = updatedTemplate.replace(regex, `{{${existingMapping.name}}}`)
      } else if (!existingNames.includes(varName)) {
        const mappingName = varName.includes('.') ? varName.split('.').pop() || varName : varName
        updatedMapping.push({ name: mappingName, value: `{{${varName}}}` })
        updatedTemplate = updatedTemplate.replace(regex, `{{${mappingName}}}`)
      }
    })
    
    const seenNames = new Set<string>()
    const finalMapping = updatedMapping.filter(item => {
      const isUsed = templateVars.some(v => item.name === v || item.value === `{{${v}}}`)
      if (!isUsed || seenNames.has(item.name)) return false
      seenNames.add(item.name)
      return true
    })
    
    isSyncingRef.current = true
    lastSyncSourceRef.current = 'template'
    prevMappingNamesRef.current = finalMapping.filter(item => item && item.name).map((item: any) => item.name)
    prevTemplateVarsRef.current = templateVars
    
    if (JSON.stringify(finalMapping) !== JSON.stringify(values.mapping)) {
      form.setFieldValue('mapping', finalMapping)
    }
    if (updatedTemplate !== String(values.template)) {
      form.setFieldValue('template', updatedTemplate)
    }
    
    setTimeout(() => { 
      isSyncingRef.current = false
      lastSyncSourceRef.current = null
    }, 50)
  }, [values?.template, selectedNode?.data?.type, form])

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
    
    // Find parent loop/iteration node if current node is a child
    const getParentLoopNode = (nodeId: string): Node | null => {
      const node = nodes.find(n => n.id === nodeId);
      if (!node) return null;
      
      const nodeData = node.getData();
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
    };
    
    const allPreviousNodeIds = getAllPreviousNodes(selectedNode.id);
    const childNodeIds = getChildNodes(selectedNode.id);
    const parentLoopNode = getParentLoopNode(selectedNode.id);
    
    console.log('childNodeIds', selectedNode, childNodeIds)
    const allRelevantNodeIds = [...allPreviousNodeIds, ...childNodeIds];
    
    // Add parent loop/iteration node variables if current node is a child
    if (parentLoopNode) {
      const parentData = parentLoopNode.getData();
      const parentNodeId = parentLoopNode.getData().id;
      
      if (parentData.type === 'loop') {
        const cycleVars = parentData.cycle_vars || [];
        cycleVars.forEach((cycleVar: any) => {
          const key = `${parentNodeId}_cycle_${cycleVar.name}`;
          if (!addedKeys.has(key)) {
            addedKeys.add(key);
            variableList.push({
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
        const itemKey = `${parentNodeId}_item`;
        const indexKey = `${parentNodeId}_index`;
        
        if (!addedKeys.has(itemKey)) {
          addedKeys.add(itemKey);
          variableList.push({
            key: itemKey,
            label: 'item',
            type: 'variable',
            dataType: 'Object',
            value: `${parentNodeId}.item`,
            nodeData: parentData,
          });
        }
        
        if (!addedKeys.has(indexKey)) {
          addedKeys.add(indexKey);
          variableList.push({
            key: indexKey,
            label: 'index',
            type: 'variable',
            dataType: 'Number',
            value: `${parentNodeId}.index`,
            nodeData: parentData,
          });
        }
      }
      
      // Check if parent loop/iteration is connected to http-request via ERROR connection
      if (parentData.type === 'loop' || parentData.type === 'iteration') {
        const parentPreviousNodeIds = getAllPreviousNodes(parentLoopNode.id);
        parentPreviousNodeIds.forEach(prevNodeId => {
          const prevNode = nodes.find(n => n.id === prevNodeId);
          if (!prevNode) return;
          
          const prevNodeData = prevNode.getData();
          if (prevNodeData.type === 'http-request') {
            // Check if connected via ERROR connection point
            const errorEdges = edges.filter(edge => {
              return edge.getTargetCellId() === parentLoopNode.id && 
              edge.getSourceCellId() === prevNodeId &&
              edge.getSourcePortId() === 'ERROR'
            });
            
            if (errorEdges.length > 0) {
              const errorMessageKey = `${prevNodeData.id}_error_message`;
              const errorTypeKey = `${prevNodeData.id}_error_type`;
              
              if (!addedKeys.has(errorMessageKey)) {
                addedKeys.add(errorMessageKey);
                variableList.push({
                  key: errorMessageKey,
                  label: 'error_message',
                  type: 'variable',
                  dataType: 'string',
                  value: `${prevNodeData.id}.error_message`,
                  nodeData: prevNodeData,
                });
              }
              
              if (!addedKeys.has(errorTypeKey)) {
                addedKeys.add(errorTypeKey);
                variableList.push({
                  key: errorTypeKey,
                  label: 'error_type',
                  type: 'variable',
                  dataType: 'string',
                  value: `${prevNodeData.id}.error_type`,
                  nodeData: prevNodeData,
                });
              }
            }
          }
        });
      }
      
      // Add variables from nodes preceding the parent loop/iteration node
      const parentPreviousNodeIds = getAllPreviousNodes(parentLoopNode.id);
      allRelevantNodeIds.push(...parentPreviousNodeIds);
    }
    
    allRelevantNodeIds.forEach(nodeId => {
      const node = nodes.find(n => n.id === nodeId);
      if (!node) return;
      
      const nodeData = node.getData();
      const dataNodeId = nodeData.id; // Use the data.id instead of node.id for consistency

      switch(nodeData.type) {
        case 'start':
          const list = [
            ...(nodeData.config?.variables?.defaultValue ?? []),
            ...(nodeData.config?.variables?.value ?? [])
          ]
          list.forEach((variable: any) => {
            if (!variable || !variable?.name) return;
            const key = `${dataNodeId}_${variable.name}`;
            if (!addedKeys.has(key)) {
              addedKeys.add(key);
              variableList.push({
                key,
                label: variable.name,
                type: 'variable',
                dataType: variable.type,
                value: `${dataNodeId}.${variable.name}`,
                nodeData: nodeData,
              });
            }
          });
          nodeData.config?.variables?.sys?.forEach((variable: any) => {
            if (!variable || !variable?.name) return;
            const key = `${dataNodeId}_sys_${variable.name}`;
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
          const llmKey = `${dataNodeId}_output`;
          if (!addedKeys.has(llmKey)) {
            addedKeys.add(llmKey);
            variableList.push({
              key: llmKey,
              label: 'output',
              type: 'variable',
              dataType: 'String',
              value: `${dataNodeId}.output`,
              nodeData: nodeData,
            });
          }
          break
        case 'knowledge-retrieval':
          const knowledgeKey = `${dataNodeId}_output`;
          if (!addedKeys.has(knowledgeKey)) {
            addedKeys.add(knowledgeKey);
            variableList.push({
              key: knowledgeKey,
              label: 'output',
              type: 'variable',
              dataType: 'array[object]',
              value: `${dataNodeId}.output`,
              nodeData: nodeData,
            });
          }
          break
        case 'parameter-extractor':
          const successKey = `${dataNodeId}___is_success`;
          const reasonKey = `${dataNodeId}___reason`;
          if (!addedKeys.has(successKey)) {
            addedKeys.add(successKey);
            variableList.push({
              key: successKey,
              label: '__is_success',
              type: 'variable',
              dataType: 'number',
              value: `${dataNodeId}.__is_success`,
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
              value: `${dataNodeId}.__reason`,
              nodeData: nodeData,
            });
          }
          // Add params variables
          const paramsList = nodeData.config?.params?.defaultValue || [];
          paramsList.forEach((param: any) => {
            if (!param || !param?.name) return;
            const paramKey = `${dataNodeId}_${param.name}`;
            if (!addedKeys.has(paramKey)) {
              addedKeys.add(paramKey);
              variableList.push({
                key: paramKey,
                label: param.name,
                type: 'variable',
                dataType: param.type || 'string',
                value: `${dataNodeId}.${param.name}`,
                nodeData: nodeData,
              });
            }
          });
          break
        case 'var-aggregator':
          if (nodeData.config.group.defaultValue) {
            // If group=true, add variables from group_variables with key as variable name
            const groupVariables = nodeData.config.group_variables.defaultValue || [];
            groupVariables?.forEach((groupVar: any) => {
              if (!groupVar || !groupVar.key) return;
              const groupVarKey = `${dataNodeId}_${groupVar.key}`;
              if (!addedKeys.has(groupVarKey)) {
                addedKeys.add(groupVarKey);
                variableList.push({
                  key: groupVarKey,
                  label: groupVar.key,
                  type: 'variable',
                  dataType: 'string',
                  value: `${dataNodeId}.${groupVar.key}`,
                  nodeData: nodeData,
                });
              }
            });
          } else {
            // If group=false, add output variable
            const varAggregatorKey = `${dataNodeId}_output`;
            if (!addedKeys.has(varAggregatorKey)) {
              addedKeys.add(varAggregatorKey);
              variableList.push({
                key: varAggregatorKey,
                label: 'output',
                type: 'variable',
                dataType: 'string',
                value: `${dataNodeId}.output`,
                nodeData: nodeData,
              });
            }
          }
          break
        case 'http-request':
          const httpBodyKey = `${dataNodeId}_body`;
          const httpStatusKey = `${dataNodeId}_status_code`;
          if (!addedKeys.has(httpBodyKey)) {
            addedKeys.add(httpBodyKey);
            variableList.push({
              key: httpBodyKey,
              label: 'body',
              type: 'variable',
              dataType: 'string',
              value: `${dataNodeId}.body`,
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
              value: `${dataNodeId}.status_code`,
              nodeData: nodeData,
            });
          }
          
          // Check if connected via ERROR connection point
          const errorEdges = edges.filter(edge => 
            edge.getTargetCellId() === selectedNode.id && 
            edge.getSourceCellId() === nodeId &&
            edge.getSourcePortId() === 'ERROR'
          );
          
          if (errorEdges.length > 0) {
            const errorMessageKey = `${dataNodeId}_error_message`;
            const errorTypeKey = `${dataNodeId}_error_type`;
            
            if (!addedKeys.has(errorMessageKey)) {
              addedKeys.add(errorMessageKey);
              variableList.push({
                key: errorMessageKey,
                label: 'error_message',
                type: 'variable',
                dataType: 'string',
                value: `${dataNodeId}.error_message`,
                nodeData: nodeData,
              });
            }
            
            if (!addedKeys.has(errorTypeKey)) {
              addedKeys.add(errorTypeKey);
              variableList.push({
                key: errorTypeKey,
                label: 'error_type',
                type: 'variable',
                dataType: 'string',
                value: `${dataNodeId}.error_type`,
                nodeData: nodeData,
              });
            }
          }
          break
        case 'jinja-render':
          const jinjaOutputKey = `${dataNodeId}_output`;
          if (!addedKeys.has(jinjaOutputKey)) {
            addedKeys.add(jinjaOutputKey);
            variableList.push({
              key: jinjaOutputKey,
              label: 'output',
              type: 'variable',
              dataType: 'string',
              value: `${dataNodeId}.output`,
              nodeData: nodeData,
            });
          }
          break
        case 'question-classifier':
          const classNameKey = `${dataNodeId}_class_name`;
          const outputKey = `${dataNodeId}_output`;
          if (!addedKeys.has(classNameKey)) {
            addedKeys.add(classNameKey);
            variableList.push({
              key: classNameKey,
              label: 'class_name',
              type: 'variable',
              dataType: 'string',
              value: `${dataNodeId}.class_name`,
              nodeData: nodeData,
            });
          }
          if (!addedKeys.has(outputKey)) {
            addedKeys.add(outputKey);
            variableList.push({
              key: outputKey,
              label: 'output',
              type: 'variable',
              dataType: 'string',
              value: `${dataNodeId}.output`,
              nodeData: nodeData,
            });
          }
          break
        case 'iteration':
          const iterationOutputKey = `${dataNodeId}_output`;
          const iterationItemKey = `${dataNodeId}_item`;
          if (!addedKeys.has(iterationOutputKey)) {
            addedKeys.add(iterationOutputKey);
            // Get the data type from the output configuration, default to string
            const outputConfig = nodeData.output;
            let outputDataType = 'string';
            if (outputConfig) {
              // Find the selected variable from variableList to get its type
              const selectedVariable = variableList.find(v => v.value === outputConfig);
              if (selectedVariable) {
                outputDataType = selectedVariable.dataType;
              }
            }
            variableList.push({
              key: iterationOutputKey,
              label: 'output',
              type: 'variable',
              dataType: outputDataType,
              value: `${dataNodeId}.output`,
              nodeData: nodeData,
            });
          }
          if (!addedKeys.has(iterationItemKey)) {
            addedKeys.add(iterationItemKey);
            variableList.push({
              key: iterationItemKey,
              label: 'item',
              type: 'variable',
              dataType: 'string',
              value: `${dataNodeId}.item`,
              nodeData: nodeData,
            });
          }
          break
        case 'loop':
          const cycleVars = nodeData.config.cycle_vars.defaultValue || [];
          console.log('cycleVars', cycleVars)
          cycleVars.forEach((cycleVar: any) => {
            const cycleVarKey = `${dataNodeId}_cycle_${cycleVar.name}`;
            if (!addedKeys.has(cycleVarKey)) {
              addedKeys.add(cycleVarKey);
              if (cycleVar.name && cycleVar.name.trim() !== '') {
                variableList.push({
                  key: cycleVarKey,
                  label: cycleVar.name,
                  type: 'variable',
                  dataType: cycleVar.type || 'string',
                  value: `${dataNodeId}.${cycleVar.name}`,
                  nodeData: nodeData,
                });
              }
            }
          });
          break
        case 'tool':
          const toolDataKey = `${dataNodeId}_data`;
          if (!addedKeys.has(toolDataKey)) {
            addedKeys.add(toolDataKey);
            variableList.push({
              key: toolDataKey,
              label: 'data',
              type: 'variable',
              dataType: 'object',
              value: `${dataNodeId}.data`,
              nodeData: nodeData,
            });
          }
          break
      }
    });

    // Add conversation variables from global config
    const conversationVariables = workflowConfig?.variables || [];
    
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
  }, [selectedNode, graphRef, workflowConfig?.variables]);

  // Filter out boolean type variables for loop and llm nodes
  const getFilteredVariableList = (nodeType?: string) => {
    if (nodeType === 'loop' || nodeType === 'llm') {
      return variableList.filter(variable => variable.dataType !== 'boolean');
    }
    return variableList;
  };

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
                  selectedNode={selectedNode}
                  graphRef={graphRef}
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
                      />
                    </Form.Item>
                  )
                }
                if (selectedNode?.data?.type === 'end' && key === 'output') {
                  return (
                    <Form.Item key={key} name={key}>
                      <MessageEditor key={key} isArray={false} parentName={key} options={variableList} />
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
                        options={getFilteredVariableList(selectedNode?.data?.type)}
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
                        options={getFilteredVariableList(selectedNode?.data?.type)}
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
                        options={getFilteredVariableList(selectedNode?.data?.type)}
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
                      <MappingList name={key} options={getFilteredVariableList(selectedNode?.data?.type)} />
                    </Form.Item>
                  
                  )
                }
                if (config.type === 'cycleVarsList') {
                  return (
                    <Form.Item key={key} name={key}>
                      <CycleVarsList
                        parentName={key}
                        options={getFilteredVariableList(selectedNode?.data?.type)}
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
                            
                            return [...getFilteredVariableList(selectedNode?.data?.type), ...loopIterationVars];
                          }
                          return getFilteredVariableList(selectedNode?.data?.type);
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
                            const baseVariableList = getFilteredVariableList(selectedNode?.data?.type);
                            // Apply filtering if specified in config
                            if (config.filterNodeTypes || config.filterVariableNames) {
                              return baseVariableList.filter(variable => {
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
                              
                              return baseVariableList.filter(variable => 
                                childNodes.some(node => node.id === variable.nodeData?.id)
                              );
                            }
                            return baseVariableList;
                          })()
                        }
                        />
                      : config.type === 'switch'
                      ? <Switch onChange={key === 'group' ? () => { form.setFieldValue('group_variables', []) } : undefined} />
                      : config.type === 'categoryList'
                      ? <CategoryList parentName={key} selectedNode={selectedNode} graphRef={graphRef} />
                      : config.type === 'conditionList'
                      ? <ConditionList
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
                          return [...variableList.filter(variable => {
                            // Keep conversation variables
                            if (variable.group === 'CONVERSATION') return true;
                            // Keep sys variables from start nodes
                            if (variable.nodeData?.type === 'start' && variable.value?.startsWith('sys.')) return true;
                            // Keep variables from non-start nodes
                            if (variable.nodeData?.type !== 'start' && variable.nodeData?.type !== 'http-request' && variable.dataType !== 'boolean') return true;
                            // Filter out custom variables from start nodes
                            return false;
                          }), ...cycleVarSuggestions];
                        })()
                      }
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