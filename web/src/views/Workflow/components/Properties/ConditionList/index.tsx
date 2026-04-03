import { type FC } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, InputNumber, Input, Divider, type SelectProps, Flex, Space, Row, Col } from 'antd'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'
import RadioGroupBtn from '../RadioGroupBtn'

interface Case {
  logical_operator: 'and' | 'or';
  expressions: Array<{ left: string; operator: string; right: string; input_type: string; }>
}

interface CaseListProps {
  value?: Case;
  onChange?: (value: Case) => void;
  options: Suggestion[];
  parentName: string;
  selectedNode?: any;
  graphRef?: any;
  addBtnText?: string;
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
    { value: 'empty', label: 'workflow.config.if-else.empty' },
    { value: 'not_empty', label: 'workflow.config.if-else.not_empty' },
  ]
}

const ConditionList: FC<CaseListProps> = ({
  options,
  parentName,
  selectedNode,
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  const handleLeftFieldChange = (index: number, newValue: string) => {
    form.setFieldsValue({
      [parentName]: {
        expressions: {
          [index]: {
            left: newValue,
            operator: undefined,
            right: undefined,
            input_type: undefined
          }
        }
      }
    });
  };

  const handleInputTypeChange = (index: number) => {
    form.setFieldValue([parentName, 'expressions', index, 'right'], undefined);
  };

  const handleChangeLogicalOperator = () => {
    const currentValue = form.getFieldValue([parentName, 'logical_operator']);
    form.setFieldValue([parentName, 'logical_operator'], currentValue === 'and' ? 'or' : 'and');
  };
  return (
    <>
      <Form.List name={[parentName, 'expressions']}>
        {(fields, { add, remove }) => {
          const logicalOperator = form.getFieldValue([parentName, 'logical_operator']);
          return (
            <>
              <div className="rb:text-[12px] rb:mb-4!">
                <Flex align="center" justify="space-between" className="rb:mb-2!">
                  <div className="rb:font-medium rb:leading-4.5">
                    {t('workflow.config.loop.condition')}
                  </div>

                  <Button
                    onClick={() => add({})}
                    className="rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]!"
                    size="small"
                  >
                    + {t('workflow.config.loop.addCondition')}
                  </Button>
                </Flex>
                <div
                  className={clsx("rb:relative", {
                    'rb:ml-15!': fields?.length > 1
                  })}
                >
                  {fields?.length > 1 && (
                    <div className="rb:absolute rb:-left-9 rb:top-4 rb:bottom-4 rb:w-6 rb:h-[calc(100%-32px)]">
                      <div className="rb:absolute rb:w-3 rb:h-[calc(50%-20px)] rb:left-5 rb:top-0 rb:z-10 rb:border-l rb:border-t rb:border-[#EBEBEB] rb:rounded-tl-[10px] rb:border-r-0"></div>
                      <div className="rb:absolute rb:z-10 rb:-right-1.25 rb:top-[calc(50%-10px)]">
                        <Form.Item name={[parentName, 'logical_operator']} noStyle >
                          <Space size={2} className="rb:cursor-pointer rb:text-[#155EEF] rb:leading-4.5 rb:font-medium rb-border rb:py-px! rb:px-1! rb:rounded-sm" onClick={handleChangeLogicalOperator}>
                            {logicalOperator}
                            <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/workflow/refresh_active.svg')]"></div>
                          </Space>
                        </Form.Item>
                      </div>
                      <div className="rb:absolute rb:w-3 rb:h-[calc(50%-20px)] rb:left-5 rb:bottom-0 rb:z-10 rb:border-l rb:border-b rb:border-[#EBEBEB] rb:rounded-bl-[10px] rb:border-r-0"></div>
                    </div>
                  )}
                  {fields.map((field, index) => {
                    const expressions = form.getFieldValue([parentName, 'expressions']) || [];
                    const currentExpression = expressions[index] || {};
                    const currentOperator = currentExpression.operator;
                    const hideRightField = currentOperator === 'empty' || currentOperator === 'not_empty';
                    const leftFieldValue = currentExpression.left;
                    const leftFieldOption = options.find(option => `{{${option.value}}}` === leftFieldValue);
                    const leftFieldType = leftFieldOption?.dataType;
                    const operatorList = operatorsObj[leftFieldType || 'default'] || operatorsObj.default || [];
                    const inputType = leftFieldType === 'number' ? currentExpression.input_type : undefined;
                    return (
                      <Flex
                        key={field.key} 
                        gap={4} 
                        align="start" 
                        className="rb:mb-2!"
                      >
                        <div className="rb:flex-1 rb:bg-[#F6F6F6] rb:rounded-lg">
                          <Row className={clsx("rb:p-1!", {
                            'rb-border-b': !hideRightField
                          })}>
                            <Col flex="1">
                              <Form.Item name={[field.name, 'left']} noStyle>
                                <VariableSelect
                                  options={options.filter(vo =>
                                    vo.value.includes('sys.') ||
                                    vo.value.includes('conv.') ||
                                    vo.nodeData.type === 'loop' ||
                                    (vo.nodeData.cycle && vo.nodeData.cycle === selectedNode?.id)
                                  )}
                                  size="small"
                                  allowClear={false}
                                  placeholder={t('common.pleaseSelect')}
                                  onChange={(val) => handleLeftFieldChange(index, val)}
                                  variant="borderless"
                                  className="rb:w-full!"
                                />
                              </Form.Item>
                            </Col>
                            <Col flex="96px">
                              <Form.Item name={[field.name, 'operator']} noStyle>
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
                                ? (
                                  <Flex align="center">
                                    <Form.Item name={[field.name, 'input_type']} noStyle>
                                      <Select
                                        placeholder={t('common.pleaseSelect')}
                                        options={[{ value: 'variable', label: 'Variable' }, { value: 'constant', label: 'Constant' }]}
                                        popupMatchSelectWidth={false}
                                        variant="borderless"
                                        className="rb:w-20!"
                                        onChange={() => handleInputTypeChange(index)}
                                      />
                                    </Form.Item>
                                    <Divider type="vertical" />
                                    <Form.Item name={[field.name, 'right']} noStyle>
                                      {inputType === 'variable'
                                        ? (
                                          <VariableSelect
                                            placeholder={t('common.pleaseSelect')}
                                            options={options.filter(vo => vo.dataType === 'number')}
                                            allowClear={false}
                                            variant="borderless"
                                            size="small"
                                          />
                                        )
                                        : (
                                          <InputNumber
                                            placeholder={t('common.pleaseEnter')}
                                            variant="borderless"
                                            className="rb:w-full!"
                                            onChange={(value) => form.setFieldValue([parentName, 'expressions', index, 'right'], value)}
                                          />
                                        )
                                      }
                                    </Form.Item>
                                  </Flex>
                                )
                                : (
                                  <Form.Item name={[field.name, 'right']} noStyle>
                                    {leftFieldType === 'boolean'
                                      ? <RadioGroupBtn options={[ { value: true, label: 'True' }, { value: false, label: 'False' }]} />
                                      : <Input variant="borderless" placeholder={t('common.pleaseEnter')} />
                                    }
                                  </Form.Item>
                                )
                              }
                            </div>
                          )}
                        </div>
                        <div
                          className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                          onClick={() => remove(field.name)}
                        ></div>
                      </Flex>
                    )
                  })}
                </div>
              </div>
            </>
          )
        }}
      </Form.List>
    </>
  )
}

export default ConditionList