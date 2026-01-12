import React from 'react';
import { useTranslation } from 'react-i18next'
import { MinusCircleOutlined } from '@ant-design/icons';
import { Button, Form, Input, Space, Row, Col } from 'antd';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'

interface MappingListProps {
  name: string;
  options: Suggestion[];
}
const MappingList: React.FC<MappingListProps> = ({ name, options }) => {
  const { t } = useTranslation()
  return (
    <>
      <Form.List name={name}>
        {(fields, { add, remove }) => (
          <>
            {fields.map(({ key, name, ...restField }) => (
              <Row key={key} gutter={12} className="rb:mb-2">
                <Col span={10}>
                  <Form.Item
                    {...restField}
                    name={[name, 'name']}
                    noStyle
                  >
                    <Input placeholder={t('common.pleaseEnter')} data-field-type="mapping-name" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item
                    {...restField}
                    name={[name, 'value']}
                    noStyle
                  >
                    <VariableSelect
                      placeholder={t('common.pleaseSelect')}
                      options={options}
                      popupMatchSelectWidth={false}
                    />
                  </Form.Item>
                </Col>
                <Col span={2}>
                  <MinusCircleOutlined onClick={() => remove(name)} />
                </Col>
              </Row>
            ))}
            <Form.Item>
              <Button type="dashed" onClick={() => add()} block>
                + {t('common.add')}
              </Button>
            </Form.Item>
          </>
        )}
      </Form.List>
    </>
  )
};

export default MappingList;