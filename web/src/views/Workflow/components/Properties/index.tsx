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

interface PropertiesProps {
  selectedNode?: Node | null; 
  setSelectedNode: (node: Node | null) => void;
  graphRef: React.MutableRefObject<Graph | undefined>;
  blankClick: () => void;
  deleteEvent: () => void;
  copyEvent: () => void;
  parseEvent: () => void;
}
const Properties: FC<PropertiesProps> = ({
  selectedNode,
  graphRef,
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

      let groupNames: Record<string, string[]> | string[] = {}

      if (group && group_names?.length) {
        group_names.forEach(vo => {
          (groupNames as Record<string, string[]>)[vo.key] = vo.value
        })
      } else if (!group) {
        groupNames = group_names?.[0]?.value || []
      }
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
          selectedNode.data.config[key].defaultValue = values[key]
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
    
    const allPreviousNodeIds = getAllPreviousNodes(selectedNode.id);
    
    allPreviousNodeIds.forEach(nodeId => {
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
                value: `{{${nodeId}.${variable.name}}}`,
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
              value: `${nodeId}.output`,
              nodeData: nodeData,
            });
          }
          break
      }
    });

    return variableList;
  }, [selectedNode, graphRef]);

  console.log('values', values)

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
              : configs && Object.keys(configs).length > 0 && Object.keys(configs).map((key) => {
                const config = configs[key] || {}

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
                  return (
                    <Form.Item key={key} name={key}>
                      <MessageEditor options={variableList} parentName={key} />
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
                        isCanAdd={!!values?.group}
                      />
                    </Form.Item>
                  
                  )
                }
                if (config.type === 'caseList') {
                  console.log('key', key)
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
                      <MappingList name={key} />
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
                        options={config.options}
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
                          options={variableList}
                        />
                      : config.type === 'switch'
                      ? <Switch />
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