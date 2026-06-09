import { useEffect, useState, useCallback, useRef, useImperativeHandle, forwardRef } from 'react';
import { Collapse, Flex, Input, InputNumber, Select, Checkbox, Button, Form, message } from 'antd';
import { CodeOutlined } from '@ant-design/icons';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';

import type { WorkflowConfig } from '../types'
import { getWorkflowDebugState, updateWorkflowNodeCache, clearWorkflowCache } from '@/api/application'
import { nodeLibrary } from '../constant'
import Empty from '@/components/Empty'
import CodeMirrorEditor from '@/components/CodeMirrorEditor';
import FileVarInput from './SingleNodeRun/FileVarInput';

const useDebounce = <T extends (...args: any[]) => void>(fn: T, delay: number) => {
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  
  const debouncedFn = useCallback((...args: Parameters<T>) => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    timeoutRef.current = setTimeout(() => {
      fn(...args);
    }, delay);
  }, [fn, delay]);

  return debouncedFn;
};

interface VariableInspectorProps {
  selectedNode: any;
  config: WorkflowConfig | null;
  onClose: () => void;
  collapsed: boolean;
  runOpen: boolean;
}

export interface VariableInspectorRef {
  refresh: () => void;
  clearCache: () => void;
}

interface VariableData {
  name: string;
  value: any;
  type?: string;
  nodeKey?: string;
}

const VariableInspector = forwardRef<VariableInspectorRef, VariableInspectorProps>(({ 
  selectedNode,
  config,
  onClose,
  collapsed,
  runOpen,
 }, ref) => {
  const { t } = useTranslation();
  const { id } = useParams()
  const [form] = Form.useForm()
  const [variables, setVariables] = useState<Record<string, any>>({});
  const [selectedVariable, setSelectedVariable] = useState<VariableData | null>(null);
  const formValues = Form.useWatch([], form);

  console.log('VariableInspector config', config)

  const handleUpdateCache = useCallback((values: Record<string, any>) => {
    if (!id || !selectedVariable || !selectedVariable?.nodeKey || !values || !Object.keys(values).length) return;

    const { type } = selectedVariable;
    const lastValue = !type?.includes('file') && typeof selectedVariable?.value === 'object'
      ? JSON.stringify(selectedVariable?.value, null, 2)
      : selectedVariable?.value;
    const currentValue = !type?.includes('file') && typeof selectedVariable?.value === 'object'
      ? JSON.parse(values[selectedVariable.nodeKey][selectedVariable.name])
      : values[selectedVariable.nodeKey][selectedVariable.name]

    if (lastValue === values[selectedVariable.nodeKey][selectedVariable.name]) return;
    setSelectedVariable(prev => {
      if (prev?.value && prev.value !== currentValue) {
        return {
          ...prev,
          value: currentValue,
        }
      }
      return prev;
    })
  
    updateWorkflowNodeCache(id, selectedVariable.nodeKey, {
      patches: [
        {
          name: selectedVariable.name,
          type: selectedVariable.type,
          value: currentValue,
        }
      ]
    });
  }, [id, selectedVariable]);

  const debouncedUpdateCache = useDebounce((values: Record<string, any>) => handleUpdateCache(values), 500);

  useEffect(() => {
    if (formValues && Object.keys(formValues).length > 0) {
      debouncedUpdateCache(formValues);
    }
  }, [formValues, debouncedUpdateCache]);

  useEffect(() => {
    getVariables()
  }, [id])

  useImperativeHandle(ref, () => ({
    refresh: getVariables,
    clearCache: handleClearCache,
  }))

  const getVariables = () => {
    if (!id) return
    getWorkflowDebugState(id)
      .then((res) => {
        const { nodes, ...rest } = (res as any).snapshot;
        const allVariables = {
          ...rest,
          ...nodes,
        };
        let newVariables: Record<string, Record<string, VariableData>> = {};
        Object.keys(allVariables).forEach(nodeKey => {
          const groupVariables = allVariables[nodeKey] || {};
          newVariables[nodeKey] = {};

          Object.keys(groupVariables).forEach(varName => {
            newVariables[nodeKey][varName] = {
              ...groupVariables[varName],
              nodeKey,

              value: groupVariables[varName].type === 'object' || (groupVariables[varName].type?.includes('file') && typeof groupVariables[varName].value === 'object')
                ? JSON.stringify(groupVariables[varName].value, null, 2)
                : groupVariables[varName].value,
            };
          })
        })
        setVariables(newVariables);
        
        if (selectedVariable) {
          const updatedVariable = flattenVariables(newVariables).find(
            v => v.name === selectedVariable.name &&
                 v.nodeKey === selectedVariable.nodeKey
          );
          if (updatedVariable) {
            form.setFieldValue([updatedVariable.nodeKey, updatedVariable.name], !updatedVariable.type?.includes('file') && typeof updatedVariable.value === 'object' ? JSON.stringify(updatedVariable.value, null, 2) : updatedVariable.value);
            setSelectedVariable(updatedVariable);
          }
        }
      })
  }

  const flattenVariables = (data: Record<string, Record<string, VariableData>> = variables): VariableData[] => {
    const result: VariableData[] = [];

    Object.entries(data).forEach(([_groupKey, groupValue]) => {
      Object.entries(groupValue).forEach(([varName, varValue]) => {
        result.push({
          name: varName,
          value: varValue.value,
          type: varValue.type,
          nodeKey: varValue.nodeKey,
        });
      });
    });

    return result;
  };

  const renderVariableValue = (variable: VariableData) => {
    const { type, value, name, nodeKey } = variable;
    const dataType = type || (Array.isArray(value) ? `array[${value.length > 0 ? typeof value[0] : 'any'}]` : typeof value);
    
    const fieldName = nodeKey ? [nodeKey, name] : name;

    return (
      <Form.Item
        name={fieldName}
        valuePropName={dataType.includes('boolean') ? "checked" : undefined}
        noStyle
      >
        {dataType.includes('file')
          ? <FileVarInput
            name={fieldName}
            dataType={dataType}
            form={form}
            defaultValue={value}
          />
          : dataType === 'object' || typeof value === 'object'
          ? <CodeMirrorEditor
            key={fieldName as string}
            language="json"
            variant="borderless"
          />
          : dataType.includes('boolean')
          ? <Checkbox>
            <span className="rb:text-xs rb:text-[#5F6368]">{String(value)}</span>
          </Checkbox>
          : dataType.includes('number')
          ? <InputNumber
            className="rb:w-full!"
            variant="borderless"
          />
          : Array.isArray(value)
          ? <Select
            mode="tags"
            className="rb:w-full!"
            options={value.map((item, index) => ({
              label: String(item),
              value: item,
              key: index,
            }))}
          />
          : <Input.TextArea
            className="rb:w-full! rb:h-full!"
            variant="borderless"
          />
        }
      </Form.Item>
    )
  };

  const handleSelectVariable = (variable: VariableData) => {
    setSelectedVariable(variable);

    setTimeout(() => {
      const { value, name, nodeKey, type } = variable;
      const fieldName = nodeKey ? [nodeKey, name] : name;
      form.setFieldValue(fieldName, !type?.includes('file') && typeof value === 'object' ? JSON.stringify(value, null, 2) : value);
    }, 0);
  };

  const renderVariableItem = (key: string, nodeKey: string, variable: VariableData) => {
    const isSelected = selectedVariable?.name === key && selectedVariable?.nodeKey === nodeKey;

    return (
      <div
        key={`${nodeKey || ''}-${key}`}
        onClick={() => handleSelectVariable({
          ...variable,
          name: key,
          nodeKey,
        })}
        className={clsx(
          "rb:flex rb:items-center rb:justify-between rb:p-2 rb:rounded-lg rb:cursor-pointer rb:transition-all",
          isSelected
            ? "rb:bg-[rgba(21,93,233,0.06)]"
            : "rb:hover:bg-[#F7F8FA]"
        )}
      >
        <div className="rb:flex rb:items-center rb:gap-2 rb:flex-1 rb:min-w-0">
          {/* {getVariableIcon(variable.name)} */}
          <span className="rb:text-sm rb:text-[#1D2129] truncate">{key}</span>
        </div>
        <span className="rb:text-xs rb:text-[#8F959E] rb:shrink-0 rb:ml-2">{variable.type}</span>
      </div>
    );
  };

  const getNodeIcon = (nodeKey: string): {
    icon: string | null;
    name: string;
  } | null => {
    const nodeInConfig = config?.nodes.find(n => n.id === nodeKey);
    const nodeInfo: {
      icon: string | null;
      name: string;
    } = {
      icon: null,
      name: nodeInConfig?.name || nodeKey,
    }
    if (!nodeInConfig) return null;
    for (const category of nodeLibrary) {
      const nodeInLib = category.nodes.find(n => n.type === nodeInConfig.type);
      if (nodeInLib?.icon) nodeInfo.icon = nodeInLib.icon;
    }
    return nodeInfo;
  };

  const renderNodeGroup = (nodeKey: string) => {
    const items = variables[nodeKey] || [];
    
    if (!Object.keys(items).length) return null;
    const nodeInfo = getNodeIcon(nodeKey);

    if (!nodeInfo && !['conversation'].includes(nodeKey)) return null;
    return (
      <Collapse.Panel
        header={
          <div className="rb:flex rb:items-center rb:gap-2">
            {nodeInfo?.icon !== 'conversation' && nodeInfo?.icon && <div className={clsx("rb:size-5 rb:bg-cover", nodeInfo.icon)} />}
            <span className="rb:text-sm rb:text-[#1D2129]">{nodeInfo?.name || nodeKey.toUpperCase()}</span>
          </div>
        }
        key={nodeKey}
        className="rb:border-none rb:bg-transparent"
      >
        <div className="rb:space-y-1">
          {Object.keys(items).map((key) => renderVariableItem(key, nodeKey, items[key]))}
        </div>
      </Collapse.Panel>
    );
  };

  const getUniqueNodeKeys = () => {
    const keys = new Set(Object.keys(variables));
    return Array.from(keys).filter(Boolean) as string[];
  };

  const handleClearCache = () => {
    if (!id) return;
    clearWorkflowCache(id)
      .then(() => {
        message.success(t('common.operateSuccess'));
        setSelectedVariable(null);
        getVariables();
      })
  };

  return (
    <div className={clsx("rb:absolute rb:bottom-5 rb:right-8 rb:h-80 rb:bg-white rb:rounded-xl rb:shadow-lg rb:border rb:border-[#E5E6EB] rb:flex rb:flex-col rb:z-999", {
      'rb:left-73': !collapsed,
      'rb:left-22': collapsed,
      'rb:right-8': !selectedNode,
      'rb:right-95.5': selectedNode,
      'rb:right-156': runOpen,
    })}>
      {Object.keys(variables).length > 0
        ? <Flex className="rb:flex-1 rb:overflow-hidden">
          {/* 左侧变量列表 */}
          <Flex vertical gap={12} className="rb:w-65 rb:border-r rb:border-[#F0F1F5] rb:overflow-y-auto rb:p-2!">
            <Flex align="center" justify="space-between">
              <div className="rb:font-medium">{t('workflow.variableInspector')}</div>
              <Button type="link" onClick={handleClearCache}>{t('workflow.resetAll')}</Button>
            </Flex>
            <Collapse 
              defaultActiveKey={getUniqueNodeKeys()} 
              className="rb:border-none rb:flex-1! rb:overflow-y-auto"
              ghost
              size="small"
            >
              {getUniqueNodeKeys().map(renderNodeGroup)}
            </Collapse>
          </Flex>

          {/* 右侧变量详情 */}
          <div className="rb:flex-1 rb:p-3 rb:overflow-y-auto">
            {selectedVariable ? (
              <Form form={form} layout="vertical" size="middle" className="rb:h-full!">
                <Flex vertical gap={12} className="rb:h-full!">
                  <Flex align="center" justify="space-between">
                    <div className="rb:flex rb:items-center rb:gap-2">
                      {selectedVariable.nodeKey && (
                        <>
                          {getNodeIcon(selectedVariable.nodeKey) && (
                            <div className={clsx("rb:size-3.5 rb:bg-cover", getNodeIcon(selectedVariable.nodeKey)?.icon)} />
                          )}
                          {getNodeIcon(selectedVariable.nodeKey)?.name} /
                        </>
                      )}
                      <span className="rb:text-sm rb:font-medium rb:text-[#1D2129]">{selectedVariable.name}</span>
                      <span className="rb:text-[10px] rb:px-1.5 rb:py-0.5 rb:bg-[rgba(21,93,233,0.1)] rb:text-[#155DE9] rb:rounded">
                        {selectedVariable.type}
                      </span>
                    </div>

                    <div className="rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/close_grey.svg')]"
                      onClick={onClose}
                    ></div>
                  </Flex>

                  <div className="rb:flex-1">
                    <div className="rb:p-3 rb:bg-[#F7F8FA] rb:rounded-lg rb:h-full! rb:overflow-auto">
                      {renderVariableValue(selectedVariable)}
                    </div>
                  </div>
                </Flex>
              </Form>
            ) : (
              <Flex vertical justify="center" className="rb:h-full!">
                <Flex justify="end">
                  <div className="rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/close_grey.svg')]"
                    onClick={onClose}
                  ></div>
                </Flex>

                <Flex vertical align="center" justify="center" className="rb:flex-1 rb:text-center">
                  <div className="rb:w-12 rb:h-12 rb:bg-[#F0F1F5] rb:rounded-full rb:flex rb:items-center rb:justify-center rb:mb-3">
                    <CodeOutlined className="rb:text-[#BBBFC4] rb:text-xl" />
                  </div>
                  <div className="rb:text-xs rb:text-[#BBBFC4]">
                    {t('workflow.selectVariable')}
                  </div>
                </Flex>
              </Flex>
            )}
          </div>
        </Flex>
        : <Flex vertical gap={12} className="rb:p-2!">
            <Flex align="center" justify="space-between" className="rb:w-full!">
              <div className="rb:font-medium">{t('workflow.variableInspector')}</div>

              <div className="rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/close_grey.svg')]"
                onClick={onClose}
              ></div>
            </Flex>
            <Empty className="rb:flex-1!" />
        </Flex>
      }
    </div>
  );
});

export default VariableInspector;