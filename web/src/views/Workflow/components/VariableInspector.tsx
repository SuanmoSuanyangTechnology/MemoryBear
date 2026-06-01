import { type FC, useEffect, useState } from 'react';
import { Collapse, Flex } from 'antd';
import { CodeOutlined } from '@ant-design/icons';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';

import type { WorkflowConfig } from '../types'
import { getWorkflowExecutionDetail } from '@/api/application'
import { nodeLibrary } from '../constant'
import Empty from '@/components/Empty'

interface VariableInspectorProps {
  selectedNode: any;
  lastExecuteId: string;
  config: WorkflowConfig | null;
  onClose: () => void;
  collapsed: boolean;
}

interface VariableData {
  name: string;
  value: any;
  type?: string;
  group: 'system' | 'conversation' | 'nodes';
  nodeKey?: string;
}

const VariableInspector: FC<VariableInspectorProps> = ({ 
  selectedNode,
  lastExecuteId,
  config,
  onClose,
  collapsed,
 }) => {
  const { t } = useTranslation();
  const { id } = useParams()
  console.log('chatHistory', config)


  const [variables, setVariables] = useState({});

  const [selectedVariable, setSelectedVariable] = useState<VariableData | null>(null);

  useEffect(() => {
    getVariables()
  }, [lastExecuteId, id])

  const getVariables = () => {
    if (!lastExecuteId || lastExecuteId === 'draft' || !id) return
    getWorkflowExecutionDetail(id, lastExecuteId)
      .then((res) => {
        setVariables((res as any).snapshot)
      })
  }

  const flattenVariables = (): VariableData[] => {
    const result: VariableData[] = [];

    Object.entries(variables).forEach(([groupKey, groupValue]) => {
      if (groupKey === 'nodes' && typeof groupValue === 'object' && groupValue !== null) {
        Object.entries(groupValue).forEach(([nodeKey, nodeValue]) => {
          if (typeof nodeValue === 'object' && nodeValue !== null) {
            Object.entries(nodeValue).forEach(([varName, varValue]) => {
              result.push({
                name: varName,
                value: varValue,
                type: Array.isArray(varValue) ? `array[${varValue.length > 0 ? typeof varValue[0] : 'any'}]` : typeof varValue,
                group: 'nodes',
                nodeKey: nodeKey,
              });
            });
          }
        });
      } else if (typeof groupValue === 'object' && groupValue !== null) {
        Object.entries(groupValue).forEach(([varName, varValue]) => {
          result.push({
            name: varName,
            value: varValue,
            type: Array.isArray(varValue) ? `array[${varValue.length > 0 ? typeof varValue[0] : 'any'}]` : typeof varValue,
            group: groupKey as 'system' | 'conversation',
          });
        });
      }
    });

    return result;
  };

  const groupedVariables = {
    system: flattenVariables().filter(v => v.group === 'system'),
    conversation: flattenVariables().filter(v => v.group === 'conversation'),
    nodes: flattenVariables().filter(v => v.group === 'nodes'),
  };

  const formatValue = (value: any): string => {
    if (value === undefined || value === null) return t('workflow.noValue');
    if (typeof value === 'object') return JSON.stringify(value, null, 2);
    return String(value);
  };

  const renderVariableItem = (variable: VariableData) => {
    const isSelected = selectedVariable?.name === variable.name && selectedVariable?.nodeKey === variable.nodeKey;

    return (
      <div
        key={`${variable.group}-${variable.nodeKey || ''}-${variable.name}`}
        onClick={() => setSelectedVariable(variable)}
        className={clsx(
          "rb:flex rb:items-center rb:justify-between rb:p-2 rb:rounded-lg rb:cursor-pointer rb:transition-all",
          isSelected
            ? "rb:bg-[rgba(21,93,233,0.06)]"
            : "rb:hover:bg-[#F7F8FA]"
        )}
      >
        <div className="rb:flex rb:items-center rb:gap-2 rb:flex-1 rb:min-w-0">
          {/* {getVariableIcon(variable.name)} */}
          <span className="rb:text-sm rb:text-[#1D2129] truncate">{variable.name}</span>
        </div>
        <span className="rb:text-xs rb:text-[#8F959E] rb:shrink-0 rb:ml-2">{variable.type}</span>
      </div>
    );
  };

  const getNodeIcon = (nodeKey: string): string | null => {
    const nodeInConfig = config?.nodes.find(n => n.id === nodeKey);
    if (!nodeInConfig) return null;
    for (const category of nodeLibrary) {
      const nodeInLib = category.nodes.find(n => n.type === nodeInConfig.type);
      if (nodeInLib?.icon) return nodeInLib.icon;
    }
    return null;
  };

  const renderNodeGroup = (nodeKey: string) => {
    const items = groupedVariables.nodes.filter(v => v.nodeKey === nodeKey);
    if (items.length === 0) return null;
    const nodeIcon = getNodeIcon(nodeKey);

    return (
      <Collapse.Panel
        header={
          <div className="rb:flex rb:items-center rb:gap-2">
            {nodeIcon && <div className={clsx("rb:size-5 rb:bg-cover", nodeIcon)} />}
            <span className="rb:text-sm rb:text-[#1D2129]">{nodeKey}</span>
          </div>
        }
        key={nodeKey}
        className="rb:border-none rb:bg-transparent"
      >
        <div className="rb:space-y-1">
          {items.map(renderVariableItem)}
        </div>
      </Collapse.Panel>
    );
  };

  const getUniqueNodeKeys = () => {
    const keys = new Set(groupedVariables.nodes.map(v => v.nodeKey));
    return Array.from(keys).filter(Boolean) as string[];
  };

  return (
    <div className={clsx("rb:absolute rb:bottom-5 rb:right-8 rb:h-80 rb:bg-white rb:rounded-xl rb:shadow-lg rb:border rb:border-[#E5E6EB] rb:flex rb:flex-col rb:z-999", {
      'rb:left-73': !collapsed,
      'rb:left-22': collapsed,
      'rb:right-8': !selectedNode,
      'rb:right-95.5': selectedNode,
    })}>
      {Object.keys(variables).length > 0
      ? <Flex className="rb:flex-1 rb:overflow-hidden">
        {/* 左侧变量列表 */}
        <Flex vertical gap={12} className="rb:w-75 rb:border-r rb:border-[#F0F1F5] rb:overflow-y-auto rb:p-2!">
          <div className="rb:font-medium">{t('workflow.variableInspector')}</div>
          <Collapse 
            defaultActiveKey={getUniqueNodeKeys()} 
            className="rb:border-none rb:flex-1! rb:overflow-y-auto"
            ghost
          >
            {getUniqueNodeKeys().map(renderNodeGroup)}
          </Collapse>
        </Flex>

        {/* 右侧变量详情 */}
        <div className="rb:flex-1 rb:p-3 rb:overflow-y-auto">
          {selectedVariable ? (
            <Flex vertical gap={12} className="rb:h-full!">
              <Flex align="center" justify="space-between">
                <div className="rb:flex rb:items-center rb:gap-2">
                  {selectedVariable.nodeKey && (
                    <>
                      {getNodeIcon(selectedVariable.nodeKey) && (
                        <div className={clsx("rb:size-3.5 rb:bg-cover", getNodeIcon(selectedVariable.nodeKey))} />
                      )}
                      {selectedVariable.nodeKey} /
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
                  <pre className="rb:text-xs rb:text-[#5F6368] rb:whitespace-pre-wrap rb:break-all">
                    {formatValue(selectedVariable.value)}
                  </pre>
                </div>
              </div>
            </Flex>
          ) : (
            <div className="rb:h-full rb:flex rb:flex-col rb:items-center rb:justify-center rb:text-center">
              <div className="rb:w-12 rb:h-12 rb:bg-[#F0F1F5] rb:rounded-full rb:flex rb:items-center rb:justify-center rb:mb-3">
                <CodeOutlined className="rb:text-[#BBBFC4] rb:text-xl" />
              </div>
              <div className="rb:text-xs rb:text-[#BBBFC4]">
                {t('workflow.selectVariable')}
              </div>
            </div>
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
};

export default VariableInspector;