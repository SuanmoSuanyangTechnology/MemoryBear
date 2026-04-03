import { type FC } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, type SelectProps, Flex, Row, Col } from 'antd'

import type { Suggestion } from '../../../Editor/plugin/AutocompletePlugin'
import RadioGroupBtn from '../../RadioGroupBtn'
import { fileSubVariable } from '../../hooks/useVariableList'
import Editor from '../../../Editor'

interface Case {
  filter_by: Array<{
    key: string;
    comparison_operator: string;
    value: string
  }>
}

interface FilterConditionsProps {
  value?: Case;
  onChange?: (value: Case) => void;
  options: Suggestion[];
  parentName: string;
  variableType?: string;
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
  ],
  type: [
    { value: 'eq', label: 'workflow.config.list-operator.type.eq' },
    { value: 'ne', label: 'workflow.config.list-operator.type.ne' },
  ]
}

const typeOptions = ['image', 'document', 'video', 'audio']

const FilterConditions: FC<FilterConditionsProps> = ({
  options,
  parentName,
  variableType,
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  const handleKeyFieldChange = (index: number, newValue: string) => {
    form.setFieldValue([parentName, 'conditions', index], {
      key: newValue,
      comparison_operator: undefined,
      value: undefined,
    });
  };

  return (
    <>
      <Form.List name={[parentName, 'conditions']}>
        {(fields, { add, remove }) => {
          return (
            <>
              <div
                className="rb:relative"
              >
                {fields.map((field, index) => {
                  const conditions = form.getFieldValue([parentName, 'conditions']) || [];
                  const currentCondition = conditions[index] || {};
                  const currentOperator = currentCondition.comparison_operator;
                  const hideValueField = currentOperator === 'empty' || currentOperator === 'not_empty';
                  const keyFieldValue = currentCondition.key;
                  const keyFieldOption = fileSubVariable.find(option => option.filed === keyFieldValue);
                  const keyFieldType = keyFieldOption?.dataType;
                  const operatorList = operatorsObj[keyFieldValue === 'type' ? 'type' : keyFieldType || 'default'] || operatorsObj.default || [];

                  return (
                    <Flex
                      key={field.key}
                      gap={4}
                      align="start"
                      className="rb:mb-2!"
                    >
                      <div className="rb:flex-1 rb:bg-[#F6F6F6] rb:rounded-lg">
                        {variableType === 'array[file]' &&
                          <Row className="rb:p-1! rb-border-b">
                            <Col span={24}>
                              <Form.Item name={[field.name, 'key']} noStyle>
                                <Select
                                  placeholder={t('common.pleaseSelect')}
                                  options={fileSubVariable}
                                  fieldNames={{ value: 'filed', label: 'label' }}
                                  onChange={(value) => handleKeyFieldChange(index, value)}
                                  variant="borderless"
                                  className="rb:w-full!"
                                />
                              </Form.Item>
                            </Col>
                          </Row>
                        }
                        <Row>
                          <Col flex={hideValueField ? '1' : "96px"}>
                            <Form.Item name={[field.name, 'comparison_operator']} noStyle>
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
                          {!hideValueField && (
                            <Col flex="1">
                              <Form.Item name={[field.name, 'value']} className="rb:pt-0.5! rb:mb-0! rb:pl-2!">
                                {variableType?.includes('boolean')
                                  ? <RadioGroupBtn options={[{ value: true, label: 'True' }, { value: false, label: 'False' }]} />
                                  : keyFieldValue === 'type'
                                  ? <Select
                                    placeholder={t('common.pleaseSelect')}
                                    options={typeOptions.map(vo => ({ value: vo, label: t(`application.${vo}`) } ))}
                                    variant="borderless"
                                    className="rb:w-full!"
                                  />
                                  : <Editor
                                    variant="borderless"
                                    type="input"
                                    size="small"
                                    height={24}
                                    options={keyFieldType ? options.flatMap(vo => {
                                        if (vo.dataType === keyFieldType) return [vo];
                                        const filteredChildren = vo.children?.filter(sub => sub.dataType === keyFieldType);
                                        if (filteredChildren?.length) return [{ ...vo, children: filteredChildren }];
                                        return [];
                                      }) : options
                                    }
                                    placeholder={t('common.pleaseEnter')}
                                  />
                                }
                              </Form.Item>
                          </Col>
                          )}
                        </Row>
                      </div>
                      <div
                        className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                        onClick={() => remove(field.name)}
                      ></div>
                    </Flex>
                  )
                })}
              </div>

              <Button
                type="dashed"
                size="middle"
                block
                onClick={() => add({})}
                className="rb:text-[12px]!"
              >
                + {t('workflow.config.list-operator.addCondition')}
              </Button>
            </>
          )
        }}
      </Form.List>
    </>
  )
}

export default FilterConditions