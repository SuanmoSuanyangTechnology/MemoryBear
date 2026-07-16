import { useEffect, type FC } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';
import { Form, Button, Select, Space, Divider, InputNumber, Flex, Row, Col } from 'antd'

import VariableSelect from '../VariableSelect'
import Editor from '../../Editor'
import { isSubExprSet } from '../../../utils';
import { typeOptions } from '../ListOperator/FilterConditions'
import type { ArrayFileSubConditionsProps } from './types'
import { fileSubFieldOperators } from './constant'


const fileSubFieldOptions = [
  { value: 'type', label: 'type' },
  { value: 'size', label: 'size' },
  { value: 'name', label: 'name' },
  { value: 'url', label: 'url' },
  { value: 'extension', label: 'extension' },
  { value: 'mime_type', label: 'mime_type' },
]

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
    <div className="rb:bg-white rb:rounded-lg rb:p-1 rb:w-60">
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
                        <Row wrap={false} className={clsx('rb:p-1!', { 'rb-border-b': !hideSubRight })}>
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
                                      onChange={(value) => {
                                        form.setFieldValue([name, caseIndex, 'expressions', conditionIndex, 'sub_variable_condition', 'conditions', subIndex, 'value'], value);
                                      }}
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

export default ArrayFileSubConditions