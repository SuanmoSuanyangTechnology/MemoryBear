import { type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, Row, Col, InputNumber, Radio, type SelectProps } from 'antd'
import { DeleteOutlined } from '@ant-design/icons';

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'
import Editor from '../../Editor'

interface Case {
  logical_operator: 'and' | 'or';
  expressions: Array<{ left: string; comparison_operator: string; right: string; input_type: string; }>
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
  ]
}

const ConditionList: FC<CaseListProps> = ({
  options,
  parentName,
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  const handleLeftFieldChange = (index: number, newValue: string) => {
    form.setFieldsValue({
      [parentName]: {
        expressions: {
          [index]: {
            left: newValue,
            comparison_operator: undefined,
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
        {(fields, { add, remove }) => (
          <div>
            <div className="rb:relative">
            {fields.map((field, index) => {
              const expressions = form.getFieldValue([parentName, 'expressions']) || [];
              const currentExpression = expressions[index] || {};
              const currentOperator = currentExpression.comparison_operator;
              const hideRightField = currentOperator === 'empty' || currentOperator === 'not_empty';
              const leftFieldValue = currentExpression.left;
              const leftFieldOption = options.find(option => `{{${option.value}}}` === leftFieldValue);
              const leftFieldType = leftFieldOption?.dataType;
              const operatorList = operatorsObj[leftFieldType || 'default'] || operatorsObj.default || [];
              const inputType = leftFieldType === 'number' ? currentExpression.input_type : undefined;
              const logicalOperator = form.getFieldValue([parentName, 'logical_operator']);
              
              return (
                <div key={field.key} className="rb:mb-3">
                  {index > 0 && (<>
                    <div className="rb:absolute rb:w-3 rb:left-2 rb:top-3.75 rb:bottom-3.75 rb:z-10 rb:border rb:border-[#DFE4ED] rb:rounded-l-md rb:border-r-0"></div>
                    <div className="rb:absolute rb:z-10 rb:left-0 rb:top-[50%] rb:transform-[translateY(-50%)]]">
                      <Form.Item name={[parentName, 'logical_operator']} noStyle >
                        <Button size="small" className="rb:cursor-pointer" onClick={handleChangeLogicalOperator}>{logicalOperator}</Button>
                      </Form.Item>
                    </div>
                  </>)}
                  
                  <div className="rb:border rb:border-[#DFE4ED] rb:rounded-md rb:p-3 rb:bg-white rb:ml-6">
                    <Row gutter={8} align="middle">
                      <Col span={14}>
                        <Form.Item name={[field.name, 'left']} noStyle>
                          <VariableSelect
                            options={options}
                            size="small"
                            allowClear={false}
                            popupMatchSelectWidth={false}
                            onChange={(val) => handleLeftFieldChange(index, val)}
                          />
                        </Form.Item>
                      </Col>
                      
                      <Col span={8}>
                        <Form.Item name={[field.name, 'comparison_operator']} noStyle>
                          <Select
                            options={operatorList.map(vo => ({
                              ...vo,
                              label: t(String(vo?.label || ''))
                            }))}
                            size="small"
                            popupMatchSelectWidth={false}
                          />
                        </Form.Item>
                      </Col>
                      <Col span={2}>
                        <DeleteOutlined
                          className="rb:text-gray-400 rb:cursor-pointer rb:hover:text-red-500"
                          onClick={() => remove(field.name)}
                        />
                      </Col>

                      {!hideRightField && <>
                        {leftFieldType === 'number'
                          ? <Col span={24}><Row>
                            <Col span={12}>
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
                            </Col>
                            <Col span={12}>
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
                                  : <InputNumber placeholder={t('common.pleaseEnter')}
                                    variant="borderless" className="rb:w-full!" />
                                }
                              </Form.Item>
                            </Col>
                          </Row></Col>
                          : <Col span={24}>
                            <Form.Item name={[field.name, 'right']} noStyle>
                              {leftFieldType === 'boolean'
                                ? <Radio.Group block>
                                  <Radio.Button value={true}>True</Radio.Button>
                                  <Radio.Button value={false}>False</Radio.Button>
                                </Radio.Group>
                                : <Editor options={options} />
                              }
                            </Form.Item>
                          </Col>
                        }
                      </>}
                      
                    </Row>
                  </div>
                </div>
              )
            })}
            </div>

            <Button
              type="dashed"
              onClick={() => add({ left: '', comparison_operator: '', right: '' })}
              className="rb:w-full rb:ml-6 rb:mt-2"
              icon={<span>+</span>}
            >
              添加条件
            </Button>
          </div>
        )}
      </Form.List>
    </>
  )
}

export default ConditionList