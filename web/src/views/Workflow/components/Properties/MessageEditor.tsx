import { type FC } from 'react';
import { Input, Form, Space, Button, Row, Col, Select, type FormListOperation } from 'antd';
import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons';

interface TextareaProps {
  parentName?: string;
  label?: string;
  placeholder?: string;
  value?: string;
  onChange?: (value?: string) => void;
}
const roleOptions = [
  // { label: 'SYSTEM', value: 'SYSTEM' },
  { label: 'USER', value: 'USER' },
  { label: 'ASSISTANT', value: 'ASSISTANT' },
]
const MessageEditor: FC<TextareaProps> = ({
  parentName = 'messages',
  placeholder,
}) => {
  const form = Form.useFormInstance();
  const values = form.getFieldsValue()

  const handleAdd = (add: FormListOperation['add']) => {
    const list = values[parentName];
    const lastRole = list[list.length - 1].role

    add({
      role: lastRole === 'USER' ? 'ASSISTANT' : 'USER',
      content: undefined
    })
  }
  
  return (
    <div>
      <Form.List name={parentName}>
        {(fields, { add, remove }) => (
          <>
            {fields.map(({ key, name, ...restField }) => {
              const currentRole = values[parentName]?.[key].role || 'USER'
              
              return (
                <Space key={key} direction="vertical" className="rb:w-full rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-2 rb:py-1.5">
                  <Row>
                    <Col span={12}>
                      <Form.Item
                        {...restField}
                        name={[name, 'role']}
                        noStyle
                      >
                        {currentRole === 'SYSTEM'
                          ? <Input disabled />
                          :
                          <Select
                            options={roleOptions}
                            disabled={currentRole === 'SYSTEM'}
                          />
                        }
                      </Form.Item>
                    </Col>
                    {currentRole !== 'SYSTEM' && <Col span={12}>
                      <div className="rb:h-full rb:flex rb:justify-end rb:items-center">
                        <MinusCircleOutlined onClick={() => remove(name)} />
                      </div>
                    </Col>}
                  </Row>
                  <Form.Item
                    {...restField}
                    name={[name, 'content']}
                    noStyle
                  >
                    <Input.TextArea placeholder={placeholder} />
                  </Form.Item>
                </Space>
              )
            })}
            <Form.Item className="rb:mt-3!">
              <Button type="dashed" onClick={() => handleAdd(add)} block icon={<PlusOutlined />}>
                Add field
              </Button>
            </Form.Item>
          </>
        )}
      </Form.List>
    </div>
  );
};

export default MessageEditor;