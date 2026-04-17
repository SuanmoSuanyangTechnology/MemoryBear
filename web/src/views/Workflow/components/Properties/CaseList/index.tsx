/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-09 18:24:53 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-17 20:47:49
 */
import { useEffect, useMemo, type FC } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, Space, Divider, InputNumber, type SelectProps, Flex, Row, Col } from 'antd'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'
import Editor from '../../Editor'
import { edgeAttrs, nodeWidth } from '../../../constant'
import RbButton from '@/components/RbButton';
import RadioGroupBtn from '../RadioGroupBtn'
import { calcConditionNodeTotalHeight, getConditionNodeCasePortY, isSubExprSet } from '../../../utils';
import { typeOptions } from '../ListOperator/FilterConditions'


interface SubCondition {
  key: string;
  operator: string;
  value?: string | number;
  input_type?: string;
}

interface SubVariableCondition {
  logical_operator: 'and' | 'or';
  conditions: SubCondition[];
}

interface Expression {
  left?: string;
  operator?: string;
  right?: string | number;
  input_type?: string;
  sub_variable_condition?: SubVariableCondition;
}

interface CaseItem {
  logical_operator: 'and' | 'or';
  expressions: Expression[];
}
interface CaseListProps {
  value?: CaseItem[];
  onChange?: (value: CaseItem[]) => void;
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
  ],
  object: [
    { value: 'eq', label: 'workflow.config.if-else.boolean.eq' },
    { value: 'ne', label: 'workflow.config.if-else.boolean.ne' },
    { value: 'empty', label: 'workflow.config.if-else.empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
  ],
  file: [
    { value: 'empty', label: 'workflow.config.if-else.file.empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.file.not_empty' },
  ],
  'array[file]': [
    { value: 'contains', label: 'workflow.config.if-else.contains' },
    { value: 'not_contains', label: 'workflow.config.if-else.not_contains' },
    { value: 'eq', label: 'workflow.config.if-else.file.eq' },
    { value: 'empty', label: 'workflow.config.if-else.empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
  ],
  'array': [
    { value: 'contains', label: 'workflow.config.if-else.contains' },
    { value: 'not_contains', label: 'workflow.config.if-else.not_contains' },
    { value: 'empty', label: 'workflow.config.if-else.empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
  ],
  'array[object]': [
    { value: 'empty', label: 'workflow.config.if-else.empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
  ]
}

const fileSubFieldOptions = [
  { value: 'type', label: 'type' },
  { value: 'size', label: 'size' },
  { value: 'name', label: 'name' },
  { value: 'url', label: 'url' },
  { value: 'extension', label: 'extension' },
  { value: 'mime_type', label: 'mime_type' },
]

export const fileSubFieldOperators: { [key: string]: SelectProps['options'] } = {
  type: [
    { value: 'eq', label: 'workflow.config.list-operator.type.eq' },
    { value: 'ne', label: 'workflow.config.list-operator.type.ne' },
  ],
  size: [
    { value: 'lt', label: 'workflow.config.if-else.num.lt' },
    { value: 'le', label: 'workflow.config.if-else.num.le' },
    { value: 'gt', label: 'workflow.config.if-else.num.gt' },
    { value: 'ge', label: 'workflow.config.if-else.num.ge' },
  ],
  default: [
    { value: 'contains', label: 'workflow.config.if-else.contains' },
    { value: 'not_contains', label: 'workflow.config.if-else.not_contains' },
    { value: 'startwith', label: 'workflow.config.if-else.startwith' },
    { value: 'endwith', label: 'workflow.config.if-else.endwith' },
    { value: 'eq', label: 'workflow.config.if-else.eq' },
    { value: 'ne', label: 'workflow.config.if-else.ne' },
    { value: 'empty', label: 'workflow.config.if-else.empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
  ],
}

interface ArrayFileSubConditionsProps {
  conditionFieldName: number;
  caseIndex: number;
  conditionIndex: number;
  name: string;
  filterNumberOptions: Suggestion[];
  options: Suggestion[];
  updateNodeLayout: (cases: any[]) => void;
  updateNodePorts: (caseCount: number, removedCaseIndex?: number) => void;
}

const ArrayFileSubConditions: FC<ArrayFileSubConditionsProps> = ({ conditionFieldName, caseIndex, conditionIndex, name, filterNumberOptions, options, updateNodeLayout, updateNodePorts }) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  const subValues = Form.useWatch([name, caseIndex, 'expressions', conditionIndex, 'sub_variable_condition', 'conditions'], form)

  const handleChangeSubLogicalOperator = () => {
    const current = form.getFieldValue([name, caseIndex, 'expressions', conditionIndex, 'sub_variable_condition', 'logical_operator']) || 'and';
    form.setFieldValue([name, caseIndex, 'expressions', conditionIndex, 'sub_variable_condition', 'logical_operator'], current === 'and' ? 'or' : 'and');
  };
  const handleInputTypeChange = (caseIndex: number, conditionIndex: number, subIndex: number) => {
    form.setFieldValue([name, caseIndex, 'expressions', conditionIndex, 'sub_variable_condition', 'conditions', subIndex, 'value'], undefined);
  };

  useEffect(() => {
    console.log('subValues', subValues)
    if (!subValues) return
    const cases = form.getFieldValue(name) || [];
    setTimeout(() => {
      updateNodeLayout(cases);
      const allSet = (subValues ?? []).every(isSubExprSet);
      console.log('allSet', allSet)
      updateNodePorts(cases.length);
    }, 100);
  }, [subValues])

  return (
    <div className="rb:bg-white rb:rounded-lg rb:p-1 rb:w-62">
      <Form.List name={[conditionFieldName, 'sub_variable_condition', 'conditions']}>
        {(subFields, { add: addSub, remove: removeSub }) => {
          const subLogicalOperator = form.getFieldValue([name, caseIndex, 'expressions', conditionIndex, 'sub_variable_condition', 'logical_operator']) || 'and';
          return (
            <>
              <div className={clsx("rb:relative", {
                'rb:ml-11': subFields.length > 1,
              })}>
                {subFields.length > 1 && (
                  <div className="rb:absolute rb:-left-8 rb:top-4 rb:bottom-4 rb:w-6 rb:h-[calc(100%-32px)]">
                    <div className="rb:absolute rb:w-2 rb:h-[calc(50%-20px)] rb:left-5 rb:top-0 rb:z-10 rb:border-l rb:border-t rb:border-[#EBEBEB] rb:rounded-tl-[10px] rb:border-r-0"></div>
                    <div className="rb:absolute rb:z-10 rb:-right-1.25 rb:top-[calc(50%-10px)]">
                        <Space size={2} className="rb:cursor-pointer rb:text-[#155EEF] rb:text-[10px] rb:leading-4.5 rb:font-medium rb-border rb:py-px! rb:px-1! rb:rounded-sm" onClick={handleChangeSubLogicalOperator}>
                          {subLogicalOperator}
                          <div className="rb:size-2.5 rb:bg-cover rb:bg-[url('@/assets/images/workflow/refresh_active.svg')]"></div>
                        </Space>
                      </div>
                    <div className="rb:absolute rb:w-2 rb:h-[calc(50%-20px)] rb:left-5 rb:bottom-0 rb:z-10 rb:border-l rb:border-b rb:border-[#EBEBEB] rb:rounded-bl-[10px] rb:border-r-0"></div>
                  </div>
                )}
                {subFields.map((subField, subIndex) => {
                  const subExpr = form.getFieldValue([name, caseIndex, 'expressions', conditionIndex, 'sub_variable_condition', 'conditions', subIndex]) || {};
                  const subOperator = subExpr.operator;
                  const subLeft = subExpr.key;
                  const subOperatorList = subLeft === 'type' ? fileSubFieldOperators.type : subLeft === 'size' ? fileSubFieldOperators.size : fileSubFieldOperators.default;
                  const hideSubRight = subOperator === 'empty' || subOperator === 'not_empty';
                  const subInputType = subExpr.input_type
                  return (
                    <Flex key={subField.key} gap={4} align="start" className="rb:mb-1.5!">
                      <div className={clsx("rb:flex-1 rb:bg-[#F6F6F6] rb:rounded-lg rb:border rb:border-[#EBEBEB]", {
                        'rb:w-43.5': subFields.length > 1,
                        'rb:w-54.5': subFields.length === 1
                      })}>
                        <Row className={clsx('rb:p-1!', { 'rb-border-b': !hideSubRight })}>
                          <Col flex="100px">
                            <Form.Item name={[subField.name, 'key']} noStyle>
                              <Select
                                options={fileSubFieldOptions}
                                size="small"
                                popupMatchSelectWidth={false}
                                placeholder={t('common.pleaseSelect')}
                                variant="borderless"
                                className="rb:w-full!"
                                onChange={(value: string) => {
                                  form.setFieldValue([name, caseIndex, 'expressions', conditionIndex, 'sub_variable_condition', 'conditions', subIndex], {
                                    key: value,
                                    input_type: value === 'size' ? 'constant' : undefined,
                                    value: undefined,
                                    operator: value === 'size' ? 'ge' : 'eq',
                                  });
                                }}
                              />
                            </Form.Item>
                          </Col>
                          <Col flex="1">
                            <Form.Item name={[subField.name, 'operator']} noStyle>
                              <Select
                                options={(subOperatorList ?? []).map(vo => ({ ...vo, label: t(String(vo?.label || '')) }))}
                                size="small"
                                popupMatchSelectWidth={false}
                                placeholder={t('common.pleaseSelect')}
                                variant="borderless"
                                className="rb:w-full!"
                              />
                            </Form.Item>
                          </Col>
                        </Row>
                        {!hideSubRight && (
                          <div>
                            {subLeft === 'size'
                              ? <Flex align="center">
                                <Form.Item name={[subField.name, 'input_type']} noStyle>
                                  <Select
                                    placeholder={t('common.pleaseSelect')}
                                    options={[{ value: 'variable', label: 'Variable' }, { value: 'constant', label: 'Constant' }]}
                                    popupMatchSelectWidth={false}
                                    variant="borderless"
                                    size="small"
                                    onChange={() => { handleInputTypeChange(caseIndex, conditionIndex, subIndex); }}
                                    className="rb:w-20!"
                                  />
                                </Form.Item>
                                <Divider type="vertical" className="rb:mx-0!" />
                                <Form.Item name={[subField.name, 'value']} noStyle>
                                  {subInputType === 'variable'
                                    ? <VariableSelect
                                      placeholder={t('common.pleaseSelect')}
                                      options={filterNumberOptions}
                                      allowClear={true}
                                      variant="borderless"
                                      size="small"
                                      className="rb:flex-1!"
                                    />
                                    : <InputNumber
                                      placeholder={t('common.pleaseEnter')}
                                      variant="borderless"
                                      className="rb:w-full!"
                                      suffix="Byte"
                                      size="small"
                                      onChange={(value) => { form.setFieldValue([name, caseIndex, 'expressions', conditionIndex, 'right'], value); }}
                                    />
                                  }
                                </Form.Item>
                              </Flex>
                              : <Form.Item name={[subField.name, 'value']} noStyle>
                                {subLeft === 'type'
                                  ? <Select
                                    placeholder={t('common.pleaseSelect')}
                                    options={typeOptions.map(vo => ({ value: vo, label: t(`application.${vo}`) }))}
                                    variant="borderless"
                                    className="rb:w-full!"
                                  />
                                  : <Editor options={options} size="small" type="input" variant='borderless' height={28} className="rb:w-full!" />
                                }
                              </Form.Item>
                            }
                          </div>
                        )}
                      </div>
                      <div
                        className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                        onClick={() => { removeSub(subField.name); }}
                      />
                    </Flex>
                  );
                })}
              </div>
              <Button
                onClick={() => { addSub({ key: undefined, operator: undefined, value: undefined, input_type: undefined }); }}
                className="rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]!"
                size="small"
              >
                + {t('workflow.config.if-else.addSubVariable')}
              </Button>
            </>
          );
        }}
      </Form.List>
    </div>
  );
};

const CaseList: FC<CaseListProps> = ({
  options,
  name,
  selectedNode,
  graphRef
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  const bringLoopChildrenToFront = (cell: any) => {
    const type = cell?.getData()?.type;
    if ((type !== 'loop' && type !== 'iteration') || !graphRef?.current) return;
    const cycleId = cell.getData().id;
    graphRef.current.getEdges().forEach((edge: any) => {
      const src = graphRef.current?.getCellById(edge.getSourceCellId());
      const tgt = graphRef.current?.getCellById(edge.getTargetCellId());
      if (src?.getData()?.cycle === cycleId || tgt?.getData()?.cycle === cycleId) edge.toFront();
    });
    graphRef.current.getNodes().forEach((n: any) => {
      if (n.getData()?.cycle === cycleId) n.toFront();
    });
  };

  // Recalculate node height and port Y positions without rebuilding ports
  const updateNodeLayout = (cases: any[]) => {
    if (!selectedNode || !graphRef?.current) return;
    selectedNode.prop('size', { width: nodeWidth, height: calcConditionNodeTotalHeight(cases) });
    cases.forEach((_c: any, i: number) => {
      selectedNode.portProp(`CASE${i + 1}`, 'args/y', getConditionNodeCasePortY(cases, i));
    });
    selectedNode.portProp(`CASE${cases.length + 1}`, 'args/y', getConditionNodeCasePortY(cases, cases.length));
  };

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

    const cases = form.getFieldValue(name) || [];
    selectedNode.prop('size', { width: nodeWidth, height: calcConditionNodeTotalHeight(cases) });

    // Add ELIF ports
    for (let i = 0; i < caseCount; i++) {
      selectedNode.addPort({
        id: `CASE${i + 1}`,
        group: 'right',
        args: {
          x: nodeWidth,
          y: getConditionNodeCasePortY(cases, i),
        },
      });
    }
    
    // Add ELSE port
    selectedNode.addPort({
      id: `CASE${caseCount + 1}`,
      group: 'right',
      args: {
        x: nodeWidth,
        y: getConditionNodeCasePortY(cases, caseCount),
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
          sourceCell.toFront()
          selectedNode.toFront()
          bringLoopChildrenToFront(sourceCell)
          bringLoopChildrenToFront(selectedNode)
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
            selectedNode.toFront()
            bringLoopChildrenToFront(selectedNode)
            targetCell.toFront()
            bringLoopChildrenToFront(targetCell)
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
      input_type: 'constant'
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

  const filterNumberOptions = useMemo(() => {
    const filterList: Suggestion[] = []
    options.forEach(vo => {
      if (vo.children && vo.children?.length > 0) {
        filterList.push({
          ...vo,
          children: vo.children.filter(child => child.dataType === 'number')
        })
      } else if (vo.dataType === 'number') {
        filterList.push(vo)
      }
    })

    return filterList
  }, [options])

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
                        <Col flex="44px">
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
                                    <Space size={2} className="rb:cursor-pointer rb:text-[#155EEF] rb:text-[10px] rb:leading-4.5 rb:font-medium rb-border rb:py-px! rb:px-1! rb:rounded-sm" onClick={() => handleChangeLogicalOperator(caseIndex)}>
                                      {logicalOperator}
                                      <div className="rb:size-2.5 rb:bg-cover rb:bg-[url('@/assets/images/workflow/refresh_active.svg')]"></div>
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
                              const leftFieldValue = currentExpression.left;
                              const leftFieldOption = options.find(option => `{{${option.value}}}` === leftFieldValue)
                                ?? options.flatMap(o => o.children ?? []).find(child => `{{${child.value}}}` === leftFieldValue)
                                ?? options.flatMap(o => o.children ?? []).flatMap((c: any) => c.children ?? []).find((gc: any) => `{{${gc.value}}}` === leftFieldValue);
                              const leftFieldType = leftFieldOption?.dataType;
                              const hideRightField = currentOperator === 'empty' || currentOperator === 'not_empty' || leftFieldType === 'file' || leftFieldType === 'array[object]';
                              const operatorList = leftFieldType && operatorsObj[leftFieldType]
                                ? operatorsObj[leftFieldType]
                                : leftFieldType && leftFieldType?.includes('array')
                                ? operatorsObj.array
                                : operatorsObj.default;
                              const inputType = leftFieldType === 'number' ? currentExpression.input_type : undefined;
                              return (
                                <Flex key={conditionField.key} gap={4} align="start" className="rb:mb-2!">
                                  <div className="rb:flex-1 rb:bg-[#F6F6F6] rb:rounded-lg">
                                    <Row className={clsx("rb:px-1!", {
                                      'rb-border-b': !hideRightField
                                    })}>
                                      <Col flex="144px">
                                        <Form.Item name={[conditionField.name, 'left']} noStyle>
                                          <VariableSelect
                                            placeholder={t('common.pleaseSelect')}
                                            options={options}
                                            size="small"
                                            allowClear={false}
                                            onChange={(val) => handleLeftFieldChange(caseIndex, conditionIndex, val as string)}
                                            variant="borderless"
                                            className="rb:w-36!"
                                          />
                                        </Form.Item>
                                      </Col>
                                      <Col flex="1">
                                        <Form.Item name={[conditionField.name, 'operator']} noStyle>
                                          <Select
                                            options={(operatorList ?? []).map(vo => ({
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
                                      <div
                                        className={clsx({
                                          "rb:py-1 rb:px-1.5": ['boolean', 'array[boolean]', 'array[file]'].includes(leftFieldType as string)
                                        })}
                                      >
                                        {leftFieldType === 'array[file]'
                                          ? <>
                                            <Form.Item name={[conditionField.name, 'sub_variable_condition', 'logical_operator']} initialValue="and" noStyle>
                                              <span />
                                            </Form.Item>
                                            <ArrayFileSubConditions
                                              conditionFieldName={conditionField.name}
                                              caseIndex={caseIndex}
                                              conditionIndex={conditionIndex}
                                              name={name}
                                              options={options}
                                              filterNumberOptions={filterNumberOptions}
                                              updateNodeLayout={updateNodeLayout}
                                              updateNodePorts={updateNodePorts}
                                            />
                                          </>
                                          : leftFieldType === 'number'
                                          ? <Flex align="center">
                                            <Form.Item name={[conditionField.name, 'input_type']} noStyle>
                                              <Select
                                                placeholder={t('common.pleaseSelect')}
                                                options={[{ value: 'variable', label: 'Variable' }, { value: 'constant', label: 'Constant' }]}
                                                popupMatchSelectWidth={false}
                                                variant="borderless"
                                                onChange={() => handleInputTypeChange(caseIndex, conditionIndex)}
                                                className="rb:w-20!"
                                              />
                                            </Form.Item>
                                            <Divider type="vertical" className="rb:mx-0!" />
                                            <Form.Item name={[conditionField.name, 'right']} noStyle>
                                              {inputType === 'variable'
                                                ? <VariableSelect
                                                  placeholder={t('common.pleaseSelect')}
                                                  options={filterNumberOptions}
                                                  allowClear={false}
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
                                              {['boolean', 'array[boolean]'].includes(leftFieldType as string)
                                                ? <RadioGroupBtn options={[{ value: true, label: 'True' }, { value: false, label: 'False' }]} type="inner" />
                                                  : <Editor options={options} size="small" type="input" variant='borderless' height={28} />
                                              }
                                            </Form.Item>
                                          )
                                        }
                                      </div>
                                    )}
                                  </div>
                                  <div
                                    className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                                    onClick={() => {
                                      removeCondition(conditionField.name);
                                      setTimeout(() => updateNodeLayout(form.getFieldValue(name) || []), 100);
                                    }}
                                  ></div>
                                </Flex>
                              )
                            })}
                          </div>
                          <Row>
                            <Col flex="1">
                              <Button
                                onClick={() => {
                                  addCondition({});
                                  setTimeout(() => updateNodeLayout(form.getFieldValue(name) || []), 100);
                                }}
                                className={clsx("rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]!")}
                                size="small"
                              >
                                + {t('workflow.config.addCase')}
                              </Button>
                            </Col>
                            {caseFields.length > 1 && <Col flex="70px">
                              <RbButton 
                                danger 
                                className="rb:group rb:mr-5 rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]! rb:gap-0!"
                                icon={<div className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/delete.svg')] rb:group-hover:bg-[url('@/assets/images/workflow/delete_hover.svg')]"></div>}
                                onClick={() => handleRemoveCase(removeCase, caseField.name, caseIndex)}
                              >
                                {t('common.remove')}
                              </RbButton>
                            </Col>}
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