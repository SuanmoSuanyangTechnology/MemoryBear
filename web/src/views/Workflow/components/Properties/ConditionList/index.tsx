import { type FC } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, InputNumber, Radio, Input, Divider, type SelectProps } from 'antd'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'

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
            <div className="rb:relative">
              <div className="rb:flex rb:items-center rb:justify-between rb:mb-2">
                <div className="rb:text-[12px] rb:font-medium rb:leading-4.5">
                  {t('workflow.config.loop.condition')}
                </div>

                <Button
                  onClick={() => add({})}
                  className="rb:py-0! rb:px-1! rb:text-[12px]!"
                  size="small"
                >
                  + {t('workflow.config.loop.addCondition')}
                </Button>
              </div>
              {fields?.length > 1 && <div className="rb:absolute rb:top-8 rb:bottom-4 rb:w-8.5 rb:h-[calc(100%-32px)]">
                <div className="rb:absolute rb:w-2.5 rb:h-[calc(50%-30px)] rb:left-5 rb:top-4 rb:z-10 rb:border-l rb:border-t rb:border-[#DFE4ED] rb:rounded-tl-[10px] rb:border-r-0"></div>
                <div className="rb:absolute rb:z-10 rb:left-0 rb:top-[calc(50%-13px)]">
                  <Form.Item name={[parentName, 'logical_operator']} noStyle >
                    <Button size="small" className="rb:text-[12px]! rb:py-px! rb:px-1! rb:w-8.5! rb:h-5!" onClick={handleChangeLogicalOperator}>{logicalOperator}</Button>
                  </Form.Item>
                </div>
                <div className="rb:absolute rb:w-2.5 rb:h-[calc(50%-30px)] rb:left-5 rb:bottom-4 rb:z-10 rb:border-l rb:border-b rb:border-[#DFE4ED] rb:rounded-bl-[10px] rb:border-r-0"></div>
              </div>}
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
                  <div key={field.key} className="rb:flex rb:items-start rb:ml-9.5 rb:mb-4">
                    <div className="rb:flex-1 rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-md">
                      <div className={clsx("rb:flex rb:gap-1 rb:p-1", {
                        'rb:border-b rb:border-b-[#DFE4ED]': !hideRightField
                      })}>
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
                            popupMatchSelectWidth={false}
                            placeholder={t('common.pleaseSelect')}
                            onChange={(val) => handleLeftFieldChange(index, val)}
                          />
                        </Form.Item>
                        <Form.Item name={[field.name, 'operator']} noStyle>
                          <Select
                            options={operatorList.map(vo => ({
                              ...vo,
                              label: t(String(vo?.label || ''))
                            }))}
                            size="small"
                            popupMatchSelectWidth={false}
                            placeholder={t('common.pleaseSelect')}
                          />
                        </Form.Item>
                      </div>

                      {!hideRightField && <div className="rb:p-1">
                        {leftFieldType === 'number'
                          ? <div className="rb:flex rb:items-center">
                            <Form.Item name={[field.name, 'input_type']} noStyle>
                              <Select
                                placeholder={t('common.pleaseSelect')}
                                options={[{ value: 'Variable', label: 'Variable' }, { value: 'Constant', label: 'Constant' }]}
                                popupMatchSelectWidth={false}
                                variant="borderless"
                                className="rb:w-full!"
                                onChange={() => handleInputTypeChange(index)}
                              />
                            </Form.Item>
                            <Divider type="vertical" />
                            <Form.Item name={[field.name, 'right']} noStyle>
                              {inputType === 'Variable'
                                ?
                                <VariableSelect
                                  placeholder={t('common.pleaseSelect')}
                                  options={options.filter(vo => vo.dataType === 'number')}
                                  allowClear={false}
                                  popupMatchSelectWidth={false}
                                  variant="borderless"
                                  className="rb:w-full!"
                                />
                                : <InputNumber
                                  placeholder={t('common.pleaseEnter')}
                                  variant="borderless"
                                  className="rb:w-full!"
                                  onChange={(value) => form.setFieldValue([parentName, 'expressions', index, 'right'], value)}
                                />
                              }
                            </Form.Item>
                          </div>
                          : <Form.Item name={[field.name, 'right']} noStyle>
                            {leftFieldType === 'boolean'
                              ? <Radio.Group block>
                                <Radio.Button value={true}>True</Radio.Button>
                                <Radio.Button value={false}>False</Radio.Button>
                              </Radio.Group>
                              : <Input placeholder={t('common.pleaseEnter')} />
                            }
                          </Form.Item>
                        }
                      </div>}
                    </div>
                    <div
                      className="rb:ml-1 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                      onClick={() => remove(field.name)}
                    ></div>
                  </div>
                )
              })}
            </div>
          )
        }}
      </Form.List>
    </>
  )
}

export default ConditionList