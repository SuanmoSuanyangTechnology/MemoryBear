import { type FC } from 'react';
import { useTranslation } from 'react-i18next'
import { Input, Form, Space, Button, Row, Col, Select, type FormListOperation } from 'antd';
import { MinusCircleOutlined } from '@ant-design/icons';
import Editor from '../Editor'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'

interface TextareaProps {
  options: Suggestion[];
  title?: string
  isArray?: boolean;
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
  title,
  isArray = true,
  parentName = 'messages',
  placeholder,
  options,
}) => {
  const { t } = useTranslation()
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
      {isArray
        ? <Form.List name={parentName}>
          {(fields, { add, remove }) => (
            <Space size={12} direction="vertical" className="rb:w-full">
              {fields.map(({ key, name, ...restField }) => {
                const currentRole = (values[parentName]?.[key].role || 'USER').toUpperCase()
                
                return (
                  <Space key={key} size={12} direction="vertical" className="rb:w-full rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-2 rb:py-1.5 rb:bg-white">
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
                      <Editor placeholder={placeholder} options={options} />
                    </Form.Item>
                  </Space>
                )
              })}
              <Form.Item>
                <Button type="dashed" onClick={() => handleAdd(add)} block>
                  +{t('workflow.addMessage')}
                </Button>
              </Form.Item>
            </Space >
          )}
        </Form.List>
        :
        <Space size={12} direction="vertical" className="rb:w-full rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-2 rb:py-1.5 rb:bg-white">
          <Row>
            <Col span={12}>
              {title ?? t('workflow.answerDesc')}
            </Col>
          </Row>
          <Form.Item
            name={parentName}
            noStyle
          >
            <Editor placeholder={placeholder} options={options} />
          </Form.Item>
        </Space>
        }
    </div>
  );
};

export default MessageEditor;