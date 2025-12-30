import React from 'react';
import { useTranslation } from 'react-i18next'
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Form, Input, Space } from 'antd';

interface MappingListProps {
  name: string;
}
const MappingList: React.FC<MappingListProps> = ({ name }) => {
  const { t } = useTranslation()
  return (
    <>
      <Form.List name={name}>
        {(fields, { add, remove }) => (
          <>
            {fields.map(({ key, name, ...restField }) => (
              <Space key={key} style={{ display: 'flex', marginBottom: 8 }} align="baseline">
                <Form.Item
                  {...restField}
                  name={[name, 'name']}
                  noStyle
                >
                  <Input placeholder={t('common.pleaseEnter')} />
                </Form.Item>
                <Form.Item
                  {...restField}
                  name={[name, 'value']}
                  noStyle
                >
                  <Input placeholder={t('common.pleaseEnter')} />
                </Form.Item>
                <MinusCircleOutlined onClick={() => remove(name)} />
              </Space>
            ))}
            <Form.Item>
              <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />}>
                Add field
              </Button>
            </Form.Item>
          </>
        )}
      </Form.List>
    </>
  )
};

export default MappingList;