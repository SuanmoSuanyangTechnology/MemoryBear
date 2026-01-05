import { type FC } from 'react'
import { useTranslation } from 'react-i18next';
import { Form, Input, Button, Row, Col, Select } from 'antd'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'

interface AssignmentListProps {
  value?: Array<{ variable_selector: string; operation: string[]; value: string;}>;
  parentName: string;
  options: Suggestion[];
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
                        options={[
                          { value: 'cover', label: t('workflow.config.assigner.cover') },
                          { value: 'clear', label: t('workflow.config.assigner.clear') },
                          { value: 'assign', label: t('workflow.config.assigner.assign') },
                        ]}
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
                        rules={[{ required: true, message: 'Missing last name' }]}
                      >
                        {operation === 'assign' ? (
                          <Input.TextArea
                            placeholder={t('common.pleaseEnter')}
                            rows={3}
                          />
                        ) : (
                          <VariableSelect
                            placeholder={t('common.pleaseSelect')}
                            options={options}
                            popupMatchSelectWidth={false}
                          />
                        )}
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