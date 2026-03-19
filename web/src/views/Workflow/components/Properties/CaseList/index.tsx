/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-09 18:24:53 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-06 14:24:57
 */
import { type FC } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, Space, Divider, InputNumber, type SelectProps, Flex, Row, Col } from 'antd'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'
import Editor from '../../Editor'
import { edgeAttrs, conditionNodeItemHeight, nodeWidth, portItemArgsY, conditionNodePortItemArgsY, conditionNodeHeight } from '../../../constant'
import RbButton from '@/components/RbButton';
import RadioGroupBtn from '../RadioGroupBtn'

interface CaseListProps {
  value?: Array<{ logical_operator: 'and' | 'or'; expressions: { left: string; operator: string; right: string; input_type?: string; }[] }>;
  onChange?: (value: Array<{ logical_operator: 'and' | 'or'; expressions: { left: string; operator: string; right: string; }[] }>) => void;
  options: Suggestion[];
  name: string;
  selectedNode?: any;
  graphRef?: any;
}
const operatorsObj: { [key: string]: SelectProps['options'] } = {
  default: [
    { value: 'empty', label: 'workflow.config.if-else.empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
    { value: 'contains', label: 'workflow.config.if-else.contains' },
    { value: 'not_contains', label: 'workflow.config.if-else.not_contains' },
    { value: 'startwith', label: 'workflow.config.if-else.startwith' },
    { value: 'endwith', label: 'workflow.config.if-else.endwith' },
    { value: 'eq', label: 'workflow.config.if-else.eq' },
    { value: 'ne', label: 'workflow.config.if-else.ne' },
  ],
  number: [
    { value: 'eq', label: 'workflow.config.if-else.num.eq' },
    { value: 'ne', label: 'workflow.config.if-else.num.ne' },
    { value: 'lt', label: 'workflow.config.if-else.num.lt' },
    { value: 'le', label: 'workflow.config.if-else.num.le' },
    { value: 'gt', label: 'workflow.config.if-else.num.gt' },
    { value: 'ge', label: 'workflow.config.if-else.num.ge' },
    { value: 'empty', label: 'workflow.config.if-else.empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
  ],
  boolean: [
    { value: 'eq', label: 'workflow.config.if-else.boolean.eq' },
    { value: 'ne', label: 'workflow.config.if-else.boolean.ne' },
  ]
}

const CaseList: FC<CaseListProps> = ({
  options,
  name,
  selectedNode,
  graphRef
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  // Update node ports based on case count changes (add/remove cases)
  const updateNodePorts = (caseCount: number, removedCaseIndex?: number) => {
    if (!selectedNode || !graphRef?.current) return;
    
    // Get current port count to determine if it's an add or remove operation
    const currentPorts = selectedNode.getPorts().filter((port: any) => port.group === 'right');
    const currentCaseCount = currentPorts.length - 1; // Exclude ELSE port
    const isAddingCase = removedCaseIndex === undefined && caseCount > currentCaseCount;
    
    // Save existing edge connections (including left-side port connections)
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
    
    // Remove all existing right-side ports
    const existingPorts = selectedNode.getPorts();
    existingPorts.forEach((port: any) => {
      if (port.group === 'right') {
        selectedNode.removePort(port.id);
      }
    });
    
    // Calculate new node height: base height 88px + 30px for each additional port
    const totalPorts = caseCount + 1; // IF/ELIF + ELSE
    const newHeight = conditionNodeHeight + (totalPorts - 2) * conditionNodeItemHeight;

    selectedNode.prop('size', { width: nodeWidth, height: newHeight })

    // Update right port x position
    currentPorts.forEach((port: any) => {
      if (port.group === 'right' && port.args) {
        selectedNode.portProp(port.id!, 'args/x', nodeWidth);
      }
    });
    // Add ELIF ports
    for (let i = 0; i < caseCount; i++) {
      selectedNode.addPort({
        id: `CASE${i + 1}`,
        group: 'right',
        args: {
          x: nodeWidth,
          y: portItemArgsY * i + conditionNodePortItemArgsY,
        },
      });
    }
    
    // Add ELSE port
    selectedNode.addPort({
      id: `CASE${caseCount + 1}`,
      group: 'right',
      args: {
        x: nodeWidth,
        y: portItemArgsY * caseCount + conditionNodePortItemArgsY,
      },
    });
    
    // Restore edge connections
    setTimeout(() => {
      edgeConnections.forEach(({ edge, sourcePortId, targetCellId, targetPortId, sourceCellId, isIncoming }: any) => {
        // If it's an incoming connection (left-side port), restore directly
        if (isIncoming) {
          const sourceCell = graphRef.current?.getCellById(sourceCellId);
          if (sourceCell) {
            graphRef.current?.addEdge({
              source: { cell: sourceCellId, port: sourcePortId },
              target: { cell: selectedNode.id, port: targetPortId },
              ...edgeAttrs,
            });
          }
          graphRef.current?.removeCell(edge);
          return;
        }
        
        // Handle right-side port connections
        const originalCaseNumber = parseInt(sourcePortId.match(/CASE(\d+)/)?.[1] || '0');
        
        // If it's a remove operation and the port is being removed, delete the connection
        if (removedCaseIndex !== undefined && originalCaseNumber === removedCaseIndex + 1) {
          graphRef.current?.removeCell(edge);
          return;
        }
        
        let newPortId = sourcePortId;
        
        // If it's a remove operation, remap port IDs
        if (removedCaseIndex !== undefined) {
          if (originalCaseNumber > removedCaseIndex + 1) {
            // Ports after the removed port, shift numbering forward
            newPortId = `CASE${originalCaseNumber - 1}`;
          }
          // ELSE port always maps to the new ELSE port position
          else if (originalCaseNumber === currentCaseCount + 1) {
            newPortId = `CASE${caseCount + 1}`;
          }
        } else if (isAddingCase) {
          // If it's an add operation, ELSE port needs to be remapped
          if (originalCaseNumber === currentCaseCount + 1) {
            newPortId = `CASE${caseCount + 1}`; // New ELSE port
          }
          // Newly added ports don't restore any connections
        }
        
        const newPorts = selectedNode.getPorts();
        const matchingPort = newPorts.find((port: any) => port.id === newPortId);
        
        if (matchingPort) {
          const targetCell = graphRef.current?.getCellById(targetCellId);
          if (targetCell) {
            graphRef.current?.addEdge({
              source: { cell: selectedNode.id, port: newPortId },
              target: { cell: targetCellId, port: targetPortId },
              ...edgeAttrs
            });
          }
        }
        
        graphRef.current?.removeCell(edge);
      });
    }, 50);
  };

  const handleChangeLogicalOperator = (index: number) => {
    const currentValue = form.getFieldValue([name, index, 'logical_operator']);
    form.setFieldValue([name, index, 'logical_operator'], currentValue === 'and' ? 'or' : 'and');
  };

  const handleLeftFieldChange = (caseIndex: number, conditionIndex: number, newValue: string) => {
    form.setFieldValue([name, caseIndex, 'expressions', conditionIndex], {
      left: newValue,
      operator: undefined,
      right: undefined,
      input_type: undefined
    });
  };

  const handleAddCase = (addCaseFunc: Function) => {
    addCaseFunc({ logical_operator: 'and', expressions: [] });
    setTimeout(() => {
      const currentCases = form.getFieldValue(name) || [];
      updateNodePorts(currentCases.length);
    }, 100);
  };

  const handleRemoveCase = (removeCaseFunc: Function, fieldName: number, caseIndex: number) => {
    removeCaseFunc(fieldName);
    setTimeout(() => {
      const currentCases = form.getFieldValue(name) || [];
      updateNodePorts(currentCases.length, caseIndex);
    }, 100);
  };

  const handleInputTypeChange = (caseIndex: number, conditionIndex: number) => {
    form.setFieldValue([name, caseIndex, 'expressions', conditionIndex, 'right'], undefined);
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
                    const logicalOperator = form.getFieldValue(name)?.[caseIndex]?.logical_operator || 'and'
                    return (
                      <Row className="rb:text-[12px] rb:mb-4!">
                        <Col flex="48px">
                          <div className="rb:font-medium rb:leading-4.5">{caseIndex === 0 ? 'IF' : 'ELIF'}</div>
                          {caseFields.length > 1 && <div className="rb:text-[10px] rb:text-[#5B6167] rb:leading-2.5"> {`CASE ${caseIndex + 1}`}</div>}
                        </Col>
                        <Col flex="1" className="rb:pl-3!">
                          <div className="rb:relative">
                            {conditionFields?.length > 1 && (
                              <div className="rb:absolute rb:-left-9 rb:top-4 rb:bottom-4 rb:w-6 rb:h-[calc(100%-32px)]">
                                <div className="rb:absolute rb:w-3 rb:h-[calc(50%-20px)] rb:left-5 rb:top-0 rb:z-10 rb:border-l rb:border-t rb:border-[#EBEBEB] rb:rounded-tl-[10px] rb:border-r-0"></div>
                                <div className="rb:absolute rb:z-10 rb:-right-1.25 rb:top-[calc(50%-10px)]">
                                  <Form.Item name={[caseField.name, 'logical_operator']} noStyle >
                                    <Space size={2} className="rb:cursor-pointer rb:text-[#155EEF] rb:leading-4.5 rb:font-medium rb-border rb:py-px! rb:px-1! rb:rounded-sm" onClick={() => handleChangeLogicalOperator(caseIndex)}>
                                      {logicalOperator}
                                      <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/workflow/refresh_active.svg')]"></div>
                                    </Space>
                                  </Form.Item>
                                </div>
                                <div className="rb:absolute rb:w-3 rb:h-[calc(50%-20px)] rb:left-5 rb:bottom-0 rb:z-10 rb:border-l rb:border-b rb:border-[#EBEBEB] rb:rounded-bl-[10px] rb:border-r-0"></div>
                              </div>
                            )}
                            {conditionFields.map((conditionField, conditionIndex) => {
                              const cases = form.getFieldValue(name) || [];
                              const currentCase = cases[caseIndex] || {};
                              const currentExpression = currentCase.expressions?.[conditionIndex] || {};
                              const currentOperator = currentExpression.operator;
                              const hideRightField = currentOperator === 'empty' || currentOperator === 'not_empty';
                              const leftFieldValue = currentExpression.left;
                              const leftFieldOption = options.find(option => `{{${option.value}}}` === leftFieldValue);
                              const leftFieldType = leftFieldOption?.dataType;
                              const operatorList = operatorsObj[leftFieldType || 'default'] || operatorsObj.default || [];
                              const inputType = leftFieldType === 'number' ? currentExpression.input_type : undefined;
                              return (
                                <Flex key={conditionField.key} gap={4} align="start" className="rb:mb-2!">
                                  <div className="rb:flex-1 rb:bg-[#F6F6F6] rb:rounded-lg">
                                    <Row className={clsx("rb:p-1!", {
                                      'rb-border-b': !hideRightField
                                    })}>
                                      <Col flex="144px">
                                        <Form.Item name={[conditionField.name, 'left']} noStyle>
                                          <VariableSelect
                                            placeholder={t('common.pleaseSelect')}
                                            options={options}
                                            size="small"
                                            allowClear={false}
                                            popupMatchSelectWidth={false}
                                            onChange={(val) => handleLeftFieldChange(caseIndex, conditionIndex, val)}
                                            variant="borderless"
                                            className="rb:w-36!"
                                          />
                                        </Form.Item>
                                      </Col>
                                      <Col flex="1">
                                        <Form.Item name={[conditionField.name, 'operator']} noStyle>
                                          <Select
                                            options={operatorList.map(vo => ({
                                              ...vo,
                                              label: t(String(vo?.label || ''))
                                            }))}
                                            size="small"
                                            popupMatchSelectWidth={false}
                                            placeholder={t('common.pleaseSelect')}
                                            variant="borderless"
                                            className="rb:w-full!"
                                          />
                                        </Form.Item>
                                      </Col>
                                    </Row>
                                    
                                    {!hideRightField && (
                                      <div className="rb:py-1 rb:px-1.5">
                                        {leftFieldType === 'number'
                                          ? <Flex align="center">
                                            <Form.Item name={[conditionField.name, 'input_type']} noStyle>
                                              <Select
                                                placeholder={t('common.pleaseSelect')}
                                                options={[{ value: 'variable', label: 'Variable' }, { value: 'Constant', label: 'constant' }]}
                                                popupMatchSelectWidth={false}
                                                variant="borderless"
                                                onChange={() => handleInputTypeChange(caseIndex, conditionIndex)}
                                                className="rb:w-20!"
                                              />
                                            </Form.Item>
                                            <Divider type="vertical" />
                                            <Form.Item name={[conditionField.name, 'right']} noStyle>
                                              {inputType === 'Variable'
                                                ? <VariableSelect
                                                  placeholder={t('common.pleaseSelect')}
                                                  options={options.filter(vo => vo.dataType === 'number')}
                                                  allowClear={false}
                                                  popupMatchSelectWidth={false}
                                                  variant="borderless"
                                                  size="small"
                                                />
                                                : <InputNumber
                                                    placeholder={t('common.pleaseEnter')}
                                                    variant="borderless"
                                                    className="rb:w-full!"
                                                    onChange={(value) => form.setFieldValue([name, caseIndex, 'expressions', conditionIndex, 'right'], value)}
                                                  />
                                              }
                                            </Form.Item>
                                          </Flex>
                                          : (
                                            <Form.Item name={[conditionField.name, 'right']} noStyle>
                                              {leftFieldType === 'boolean'
                                                ? <RadioGroupBtn options={[ { value: true, label: 'True' }, { value: false, label: 'False' }]} type="inner" />
                                                : <Editor options={options} size="small" type="input" />
                                              }
                                            </Form.Item>
                                          )
                                        }
                                      </div>
                                    )}
                                  </div>
                                  <div
                                    className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                                    onClick={() => removeCondition(conditionField.name)}
                                  ></div>
                                </Flex>
                              )
                            })}
                          </div>
                          <Row>
                            <Col flex="1">
                              <Button
                                onClick={() => addCondition({})}
                                className={clsx("rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]!")}
                                size="small"
                              >
                                + {t('workflow.config.addCase')}
                              </Button>
                            </Col>
                            <Col flex="70px">
                              <RbButton 
                                danger 
                                className="rb:group rb:mr-5 rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]! rb:gap-0!"
                                icon={<div className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/delete.svg')] rb:group-hover:bg-[url('@/assets/images/workflow/delete_hover.svg')]"></div>}
                                onClick={() => handleRemoveCase(removeCase, caseField.name, caseIndex)}
                              >
                                {t('common.remove')}
                              </RbButton>
                            </Col>
                          </Row>
                        </Col>
                      </Row>
                    )
                  }}
                </Form.List>
              </div>
            ))}
            
            <Button 
              type="dashed" 
              block
              size="middle"
              className="rb:text-[12px]!"
              onClick={() => handleAddCase(addCase)}
            >
              + ELIF
            </Button>
          </>
        )}
      </Form.List>
      
      <div className="rb:font-medium rb:text-[12px] rb:mt-4 rb:leading-4.5">ELSE</div>
      <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-2 rb:leading-4.5">{t('workflow.config.if-else.else_desc')}</div>
    </>
  )
}

export default CaseList