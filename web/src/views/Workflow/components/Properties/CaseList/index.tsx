import { type FC } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, Space, Divider, InputNumber, Radio, type SelectProps } from 'antd'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'
import Editor from '../../Editor'
import { edgeAttrs, portArgs } from '../../../constant'

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

  const updateNodePorts = (caseCount: number, removedCaseIndex?: number) => {
    if (!selectedNode || !graphRef?.current) return;
    
    // 获取当前端口数量来判断是添加还是删除操作
    const currentPorts = selectedNode.getPorts().filter((port: any) => port.group === 'right');
    const currentCaseCount = currentPorts.length - 1; // 减去ELSE端口
    const isAddingCase = removedCaseIndex === undefined && caseCount > currentCaseCount;
    
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
      args: portArgs,
      attrs: { text: { text: 'IF', fontSize: 12, fill: '#5B6167' }}
    });
    
    // 添加 ELIF 端口
    for (let i = 1; i < caseCount; i++) {
      selectedNode.addPort({
        id: `CASE${i + 1}`,
        group: 'right',
        args: portArgs,
        attrs: { text: { text: 'ELIF', fontSize: 12, fill: '#5B6167' }}
      });
    }
    
    // 添加 ELSE 端口
    selectedNode.addPort({
      id: `CASE${caseCount + 1}`,
      group: 'right',
      args: portArgs,
      attrs: { text: { text: 'ELSE', fontSize: 12, fill: '#5B6167' }}
    });
    
    // 恢复连线
    setTimeout(() => {
      edgeConnections.forEach(({ edge, sourcePortId, targetCellId, targetPortId, sourceCellId, isIncoming }: any) => {
        // 如果是进入连线（左侧端口），直接恢复
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
        
        // 处理右侧端口连线
        const originalCaseNumber = parseInt(sourcePortId.match(/CASE(\d+)/)?.[1] || '0');
        
        // 如果是删除操作且是被删除的端口，删除连线
        if (removedCaseIndex !== undefined && originalCaseNumber === removedCaseIndex + 1) {
          graphRef.current?.removeCell(edge);
          return;
        }
        
        let newPortId = sourcePortId;
        
        // 如果是删除操作，需要重新映射端口ID
        if (removedCaseIndex !== undefined) {
          if (originalCaseNumber > removedCaseIndex + 1) {
            // 被删除端口之后的端口，编号向前移动
            newPortId = `CASE${originalCaseNumber - 1}`;
          }
          // ELSE端口始终映射到新的ELSE端口位置
          else if (originalCaseNumber === currentCaseCount + 1) {
            newPortId = `CASE${caseCount + 1}`;
          }
        } else if (isAddingCase) {
          // 如果是添加操作，ELSE端口需要重新映射
          if (originalCaseNumber === currentCaseCount + 1) {
            newPortId = `CASE${caseCount + 1}`; // 新的ELSE端口
          }
          // 新添加的端口不恢复任何连线
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
    form.setFieldsValue({
      [name]: {
        [caseIndex]: {
          expressions: {
            [conditionIndex]: {
              left: newValue,
              operator: undefined,
              right: undefined,
              input_type: undefined
            }
          }
        }
      }
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
                      <div className={clsx("rb:relative")}>
                        <div className="rb:flex rb:items-center rb:justify-between rb:mb-2">
                          <div className="rb:text-[12px] rb:leading-4.5">
                            <span className="rb:font-medium ">{caseIndex === 0 ? 'IF' : 'ELIF'}</span>
                            {caseFields.length > 1 && <span className="rb:text-[10px] rb:text-[#5B6167]"> ({`CASE ${caseIndex + 1}`})</span>}
                          </div>

                          <Space>
                            <Button
                              onClick={() => addCondition({})}
                              className="rb:py-0! rb:px-1! rb:text-[12px]!"
                              size="small"
                            >
                              + {t('workflow.config.addCase')}
                            </Button>
                            {caseFields.length > 1 &&
                              <Button
                                className="rb:py-0! rb:px-1! rb:text-[12px]!"
                                onClick={() => handleRemoveCase(removeCase, caseField.name, caseIndex)}
                              >
                                {t('common.remove')}
                              </Button>
                            }
                          </Space>
                        </div>
                        {conditionFields?.length > 1 && <div className="rb:absolute rb:top-8 rb:bottom-4 rb:w-8.5 rb:h-[calc(100%-32px)]">
                          <div className="rb:absolute rb:w-2.5 rb:h-[calc(50%-30px)] rb:left-5 rb:top-4 rb:z-10 rb:border-l rb:border-t rb:border-[#DFE4ED] rb:rounded-tl-[10px] rb:border-r-0"></div>
                          <div className="rb:absolute rb:z-10 rb:left-0 rb:top-[calc(50%-13px)]">
                            <Form.Item name={[caseField.name, 'logical_operator']} noStyle >
                              <Button size="small" className="rb:text-[12px]! rb:py-px! rb:px-1! rb:w-8.5! rb:h-5!" onClick={() => handleChangeLogicalOperator(caseIndex)}>{logicalOperator}</Button>
                            </Form.Item>
                          </div>
                          <div className="rb:absolute rb:w-2.5 rb:h-[calc(50%-30px)] rb:left-5 rb:bottom-4 rb:z-10 rb:border-l rb:border-b rb:border-[#DFE4ED] rb:rounded-bl-[10px] rb:border-r-0"></div>
                        </div>}
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
                            <div key={conditionField.key} className="rb:flex rb:items-start rb:ml-9.5 rb:mb-4">
                              <div className="rb:flex-1 rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-md">
                                <div className={clsx("rb:flex rb:gap-1 rb:p-1", {
                                  'rb:border-b rb:border-b-[#DFE4ED]': !hideRightField
                                })}>
                                  <Form.Item name={[conditionField.name, 'left']} noStyle>
                                    <VariableSelect
                                      placeholder={t('common.pleaseSelect')}
                                      options={options}
                                      size="small"
                                      allowClear={false}
                                      popupMatchSelectWidth={false}
                                      onChange={(val) => handleLeftFieldChange(caseIndex, conditionIndex, val)}
                                      className="rb:bg-white! rb:w-29.5!"
                                    />
                                  </Form.Item>
                                  <Form.Item name={[conditionField.name, 'operator']} noStyle>
                                    <Select
                                      options={operatorList.map(vo => ({
                                        ...vo,
                                        label: t(String(vo?.label || ''))
                                      }))}
                                      size="small"
                                      popupMatchSelectWidth={false}
                                      placeholder={t('common.pleaseSelect')}
                                      className="rb:bg-white! rb:w-22!"
                                    />
                                  </Form.Item>
                                </div>
                                
                                {!hideRightField && <div className="rb:p-1">
                                  {leftFieldType === 'number'
                                    ? <div className="rb:flex rb:items-center">
                                      <Form.Item name={[conditionField.name, 'input_type']} noStyle>
                                        <Select
                                          placeholder={t('common.pleaseSelect')}
                                          options={[{ value: 'Variable', label: 'Variable' }, { value: 'Constant', label: 'Constant' }]}
                                          popupMatchSelectWidth={false}
                                          variant="borderless"
                                          onChange={() => handleInputTypeChange(caseIndex, conditionIndex)}
                                          className="rb:w-18!"
                                        />
                                      </Form.Item>
                                      <Divider type="vertical" />
                                      <Form.Item name={[conditionField.name, 'right']} noStyle>
                                        {inputType === 'Variable'
                                          ?
                                          <VariableSelect
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
                                    </div>
                                    : <Form.Item name={[conditionField.name, 'right']} noStyle>
                                      {leftFieldType === 'boolean'
                                          ? <Radio.Group block>
                                            <Radio.Button value={true}>True</Radio.Button>
                                            <Radio.Button value={false}>False</Radio.Button>
                                          </Radio.Group>
                                          : <Editor options={options} size="small" type="input" />
                                      }
                                    </Form.Item>
                                  }
                                </div>}
                              </div>
                              <div
                                className="rb:ml-1 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                                onClick={() => removeCondition(conditionField.name)}
                              ></div>
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