import { type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Input, Row, Col, Select, InputNumber, Radio } from 'antd'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'

interface AssignmentListProps {
  value?: Array<{ variable_selector: string; operation: string[]; value: string;}>;
  parentName: string;
  options: Suggestion[];
}

const operationsObj = {
  number: [
    { value: 'cover', label: 'workflow.config.assigner.cover' },
    { value: 'clear', label: 'workflow.config.assigner.clear' },
    { value: 'assign', label: 'workflow.config.assigner.assign' },
    { value: 'add', label: 'workflow.config.assigner.add' },
    { value: 'subtract', label: 'workflow.config.assigner.subtract' },
    { value: 'multiply', label: 'workflow.config.assigner.multiply' },
    { value: 'divide', label: 'workflow.config.assigner.divide' },
  ],
  default: [
    { value: 'cover', label: 'workflow.config.assigner.cover' },
    { value: 'clear', label: 'workflow.config.assigner.clear' },
    { value: 'assign', label: 'workflow.config.assigner.assign' },
  ],
}

const AssignmentList: FC<AssignmentListProps> = ({
  parentName,
  options = [],
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();

  return (
    <Form.List name={parentName}>
      {(fields, { add, remove }) => (
        <>
          <div className="rb:flex rb:justify-between">
            {t(`workflow.config.assigner.${parentName}`)}
            <PlusOutlined onClick={() => add({ operation: 'cover'})} />
          </div>
          {fields.map(({ key, name, ...restField }) => {
            const variableSelector = form.getFieldValue([parentName, name, 'variable_selector']);
            const selectedOption = options.find(option => `{{${option.value}}}` === variableSelector);
            const dataType = selectedOption?.dataType;
            const operationOptions = dataType === 'number' ? operationsObj.number : operationsObj.default;
            
            return (
              <div key={key} className="rb:mb-4">
                <Row gutter={12} className="rb:mb-2!">
                  <Col span={14}>
                    <Form.Item
                      {...restField}
                      name={[name, 'variable_selector']}
                      noStyle
                    >
                      <VariableSelect
                        placeholder={t('common.pleaseSelect')}
                        options={options}
                        popupMatchSelectWidth={false}
                        onChange={() => {
                          form.setFieldValue([parentName, name, 'operation'], undefined);
                          form.setFieldValue([parentName, name, 'value'], undefined);
                        }}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item
                      {...restField}
                      name={[name, 'operation']}
                      noStyle
                    >
                      <Select
                        placeholder={t('common.pleaseSelect')}
                        options={operationOptions.map(op => ({
                          ...op,
                          label: t(op.label)
                        }))}
                        popupMatchSelectWidth={false}
                        onChange={() => {
                          form.setFieldValue([parentName, name, 'value'], undefined);
                        }}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={2} className="rb:flex! rb:items-center rb:justify-end">
                    <MinusCircleOutlined onClick={() => remove(name)} />
                  </Col>
                </Row>

                <Form.Item shouldUpdate noStyle>
                  {(form) => {
                    const operation = form.getFieldValue([parentName, name, 'operation']);
                    if (operation === 'clear') return null;
                    
                    return (
                      <Form.Item
                        {...restField}
                        name={[name, 'value']}
                        noStyle
                      >
                        {dataType === 'number' && operation === 'cover'
                          ? <VariableSelect
                            placeholder={t('common.pleaseSelect')}
                            options={dataType ? options.filter(vo => vo.dataType === dataType) : options}
                            popupMatchSelectWidth={false}
                          />
                          : dataType === 'number'
                          ? <InputNumber
                            placeholder={t('common.pleaseEnter')}
                            className="rb:w-full!"
                            onChange={(value) => form.setFieldValue([name, 'value'], value)}
                          />
                          : operation === 'assign'
                          ? <>
                            {dataType === 'boolean'
                              ? <Radio.Group block>
                                <Radio.Button value={true}>True</Radio.Button>
                                <Radio.Button value={false}>False</Radio.Button>
                              </Radio.Group>
                              : <Input.TextArea
                                placeholder={t('common.pleaseEnter')}
                                rows={3}
                              />
                            }
                          </>
                          : <VariableSelect
                            placeholder={t('common.pleaseSelect')}
                            options={dataType ? options.filter(vo => vo.dataType === dataType) : options}
                            popupMatchSelectWidth={false}
                          />
                        }
                      </Form.Item>
                    );
                  }}
                </Form.Item>
              </div>
            )
          })}
        </>
      )}
    </Form.List>
  )
}

export default AssignmentList