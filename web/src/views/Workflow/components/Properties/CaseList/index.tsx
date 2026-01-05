import { type FC } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, Space, Row, Col, Divider } from 'antd'
import { DeleteOutlined } from '@ant-design/icons';

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'
import Editor from '../../Editor'

interface CaseListProps {
  value?: Array<{ logical_operator: 'and' | 'or'; expressions: { left: string; comparison_operator: string; right: string; }[] }>;
  onChange?: (value: Array<{ logical_operator: 'and' | 'or'; expressions: { left: string; comparison_operator: string; right: string; }[] }>) => void;
  options: Suggestion[];
  name: string;
  selectedNode?: any;
  graphRef?: any;
}
const operatorList = [
  "empty",
  "not_empty",
  "contains",
  "not_contains",
  "startwith",
  "endwith",
  "eq",
  "ne",
  "lt",
  "le",
  "gt",
  "ge"
]

const CaseList: FC<CaseListProps> = ({
  value = [],
  options,
  name,
  onChange,
  selectedNode,
  graphRef
}) => {
  const { t } = useTranslation();

  const updateNodePorts = (caseCount: number, removedCaseIndex?: number) => {
    if (!selectedNode || !graphRef?.current) return;
    
    // 保存现有连线信息（包括左侧端口连线）
    const existingEdges = graphRef.current.getEdges().filter((edge: any) => 
      edge.getSourceCellId() === selectedNode.id || edge.getTargetCellId() === selectedNode.id
    );
    const edgeConnections = existingEdges.map((edge: any) => ({
      edge,
      sourcePortId: edge.getSourcePortId(),
      targetCellId: edge.getTargetCellId(),
      targetPortId: edge.getTargetPortId(),
      sourceCellId: edge.getSourceCellId(),
      isIncoming: edge.getTargetCellId() === selectedNode.id
    }));
    
    // 移除所有现有的右侧端口
    const existingPorts = selectedNode.getPorts();
    existingPorts.forEach((port: any) => {
      if (port.group === 'right') {
        selectedNode.removePort(port.id);
      }
    });
    
    // 计算新的节点高度：基础高度88px + 每个额外port增加30px
    const baseHeight = 88;
    const totalPorts = caseCount + 1; // IF/ELIF + ELSE
    const newHeight = baseHeight + (totalPorts - 2) * 30;

    selectedNode.prop('size', { width: 240, height: newHeight })
    
    // 添加 IF 端口
    selectedNode.addPort({
      id: 'CASE1',
      group: 'right',
      args: { dy: 24 },
      attrs: { text: { text: 'IF', fontSize: 12, fill: '#5B6167' }}
    });
    
    // 添加 ELIF 端口
    for (let i = 1; i < caseCount; i++) {
      selectedNode.addPort({
        id: `CASE${i + 1}`,
        group: 'right',
        attrs: { text: { text: 'ELIF', fontSize: 12, fill: '#5B6167' }}
      });
    }
    
    // 添加 ELSE 端口
    selectedNode.addPort({
      id: `CASE${caseCount + 1}`,
      group: 'right',
      attrs: { text: { text: 'ELSE', fontSize: 12, fill: '#5B6167' }}
    });
    
    // 恢复仍然存在的端口连线
    setTimeout(() => {
      edgeConnections.forEach(({ edge, sourcePortId, targetCellId, targetPortId, sourceCellId, isIncoming }: any) => {
        // 如果是进入连线（左侧端口），直接恢复
        if (isIncoming) {
          const sourceCell = graphRef.current?.getCellById(sourceCellId);
          if (sourceCell) {
            graphRef.current?.addEdge({
              source: { cell: sourceCellId, port: sourcePortId },
              target: { cell: selectedNode.id, port: targetPortId },
              attrs: {
                line: {
                  stroke: '#155EEF',
                  strokeWidth: 1,
                  targetMarker: {
                    name: 'block',
                    size: 8,
                  },
                },
              },
            });
          }
          graphRef.current?.removeCell(edge);
          return;
        }
        
        // 处理右侧端口连线
        const originalCaseNumber = parseInt(sourcePortId.match(/CASE(\d+)/)?.[1] || '0');
        
        // 如果是被删除的端口，不重新创建连线
        if (removedCaseIndex !== undefined && originalCaseNumber === removedCaseIndex + 1) {
          graphRef.current?.removeCell(edge);
          return;
        }
        
        let newPortId = sourcePortId;
        
        // 如果是原来的ELSE端口，重新映射到新的ELSE端口
        const maxOriginalCaseNumber = Math.max(...edgeConnections
          .filter(({ isIncoming }: any) => !isIncoming)
          .map(({ sourcePortId }: any) => {
            const match = sourcePortId.match(/CASE(\d+)/);
            return match ? parseInt(match[1]) : 0;
          }));
        
        if (originalCaseNumber === maxOriginalCaseNumber) {
          newPortId = `CASE${caseCount + 1}`; // 新的ELSE端口
        } else if (removedCaseIndex !== undefined && originalCaseNumber > removedCaseIndex + 1) {
          // 如果是被删除端口之后的端口，编号向前移动
          newPortId = `CASE${originalCaseNumber - 1}`;
        }
        
        const newPorts = selectedNode.getPorts();
        const matchingPort = newPorts.find((port: any) => port.id === newPortId);
        
        if (matchingPort) {
          const targetCell = graphRef.current?.getCellById(targetCellId);
          if (targetCell) {
            graphRef.current?.addEdge({
              source: { cell: selectedNode.id, port: newPortId },
              target: { cell: targetCellId, port: targetPortId },
              attrs: {
                line: {
                  stroke: '#155EEF',
                  strokeWidth: 1,
                  targetMarker: {
                    name: 'block',
                    size: 8,
                  },
                },
              },
            });
          }
        }
        
        graphRef.current?.removeCell(edge);
      });
    }, 50);
  };
  const handleChangeLogicalOperator = (index: number) => {
    const newValue = [...value]
    newValue[index] = {
      ...newValue[index],
      logical_operator: newValue[index].logical_operator === 'and' ? 'or' : 'and'
    }
    onChange && onChange(newValue)
  }

  const handleAddCase = (addCaseFunc: Function) => {
    addCaseFunc({ logical_operator: 'and', expressions: [] });
    setTimeout(() => {
      updateNodePorts((value?.length || 0) + 1);
    }, 100);
  };

  const handleRemoveCase = (removeCaseFunc: Function, fieldName: number, caseIndex: number) => {
    removeCaseFunc(fieldName);
    setTimeout(() => {
      updateNodePorts((value?.length || 1) - 1, caseIndex);
    }, 100);
  };

  return (
    <>
      <Form.List name={name}>
        {(caseFields, { add: addCase, remove: removeCase }) => (
          <>
            {caseFields.map((caseField, caseIndex) => (
              <div key={caseField.key}>
                <Form.List name={[caseField.name, 'expressions']}>
                  {(conditionFields, { add: addCondition, remove: removeCondition }) => {
                    return (
                      <div className={clsx("rb:relative rb:mb-4 rb:border rb:border-gray-200 rb:rounded rb:p-3 rb:pl-5")}>
                        <div className="rb:flex rb:items-center rb:justify-between rb:mb-3">
                          <span className="rb:font-medium">
                            {caseIndex === 0 ? 'IF' : 'ELIF'}<br/>
                            {caseFields.length > 1 && <span className="rb:text-[10px] rb:text-[#5B6167]">{`CASE ${caseIndex + 1}`}</span>}
                          </span>

                          <Space>
                            <Button
                              type="dashed"
                              onClick={() => addCondition()}
                              size="small"
                            >
                              + {t('workflow.config.addCase')}
                            </Button>
                            {caseFields.length > 1 && <DeleteOutlined
                              className="rb:text-[12px]"
                              onClick={() => handleRemoveCase(removeCase, caseField.name, caseIndex)}
                            />}
                          </Space>
                        </div>
                        {conditionFields?.length > 1 &&
                        <>
                          <div className="rb:absolute rb:w-3 rb:left-2 rb:top-15 rb:bottom-6 rb:z-10 rb:border rb:border-[#DFE4ED] rb:rounded-l-md rb:border-r-0"></div>
                          <div className="rb:absolute rb:z-10 rb:left-0 rb:top-[50%] rb:transform-[translateY(-50%)]]">
                            <Form.Item name={[caseField.name, 'logical_operator']} noStyle >
                              <Button size="small" className="rb:cursor-pointer" onClick={() => handleChangeLogicalOperator(caseIndex)}>{value?.[caseIndex].logical_operator}</Button>
                            </Form.Item>
                          </div>
                        </>
                        }
                        {conditionFields.map((conditionField, conditionIndex) => {
                          const currentOperator = value?.[caseIndex]?.expressions?.[conditionIndex]?.comparison_operator;
                          const hideRightField = currentOperator === 'empty' || currentOperator === 'not_empty';
                          
                          return (
                            <div key={conditionField.key} className={clsx({
                              "rb:mb-3": conditionIndex !== conditionFields.length - 1
                            })}>
                              <div className="rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-2 rb:py-1.5 rb:bg-white">
                                <Row gutter={12} className="rb:mb-1">
                                  <Col span={14}>
                                    <Form.Item name={[conditionField.name, 'left']} noStyle>
                                      <VariableSelect
                                        placeholder={t('common.pleaseSelect')}
                                        options={options}
                                        size="small"
                                        allowClear={false}
                                        popupMatchSelectWidth={false}
                                      />
                                    </Form.Item>
                                  </Col>
                                  <Col span={8}>
                                    <Form.Item name={[conditionField.name, 'comparison_operator']} noStyle>
                                      <Select
                                        options={operatorList.map(key => ({
                                          value: key,
                                          label: t(`workflow.config.if-else.${key}`)
                                        }))}
                                        size="small"
                                        popupMatchSelectWidth={false}
                                      />
                                    </Form.Item>
                                  </Col>
                                  <Col span={2}>
                                    <DeleteOutlined
                                      className="rb:text-[12px]"
                                      onClick={() => removeCondition(conditionField.name)}
                                    />
                                  </Col>
                                </Row>
                                
                                {!hideRightField && (
                                  <Form.Item name={[conditionField.name, 'right']} noStyle>
                                    <Editor options={options} />
                                  </Form.Item>
                                )}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )
                  }}
                </Form.List>
              </div>
            ))}
            
            <Button 
              type="dashed" 
              block
              onClick={() => handleAddCase(addCase)}
            >
              + ELIF
            </Button>
          </>
        )}
      </Form.List>
      <Divider />
      <div className="rb:font-medium">ELSE</div>
      <div className="rb:text-[12px] rb:text-[#5B6167] ">{t('workflow.config.if-else.else_desc')}</div>
    </>
  )
}

export default CaseList