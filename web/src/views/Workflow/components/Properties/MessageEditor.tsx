import { type FC, useMemo } from 'react';
import { useTranslation } from 'react-i18next'
import { Input, Form, Space, Button, Row, Col, Select, type FormListOperation } from 'antd';
import { MinusCircleOutlined } from '@ant-design/icons';
import Editor from '../Editor'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'

interface MessageEditor {
  options: Suggestion[];
  title?: string
  isArray?: boolean;
  parentName?: string | string[];
  label?: string;
  placeholder?: string;
  value?: string;
  enableJinja2?: boolean;
  onChange?: (value?: string) => void;
}
const roleOptions = [
  // { label: 'SYSTEM', value: 'SYSTEM' },
  { label: 'USER', value: 'USER' },
  { label: 'ASSISTANT', value: 'ASSISTANT' },
]
const MessageEditor: FC<MessageEditor> = ({
  title,
  isArray = true,
  parentName = 'messages',
  placeholder,
  options,
  enableJinja2 = false,
}) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance();
  const values = Form.useWatch([], form);

  // 检查是否已经使用了context变量，将已使用的context设置为disabled
  const processedOptions = useMemo(() => {
    if (!isArray) return options;
    
    // 获取表单中对应字段的值
    const fieldValue = Array.isArray(parentName) 
      ? parentName.reduce((obj, key) => obj?.[key], values)
      : values?.[parentName];
    
    if (!fieldValue) return options;
    
    // 获取所有消息内容
    const allContents = fieldValue
      .map((msg: any) => msg?.content || '')
      .join(' ');
    
    // 将已使用的context变量标记为disabled
    return options.map(opt => {
      if (opt.isContext && allContents.includes(opt.value)) {
        return { ...opt, disabled: true };
      }
      return opt;
    });
  }, [options, values, parentName, isArray]);

  const handleAdd = (add: FormListOperation['add']) => {
    const fieldValue = Array.isArray(parentName) 
      ? parentName.reduce((obj, key) => obj?.[key], values)
      : values?.[parentName];
    
    const list = fieldValue || [];
    const lastRole = list.length > 0 ? list[list.length - 1]?.role : 'ASSISTANT';

    add({
      role: lastRole === 'USER' ? 'ASSISTANT' : 'USER',
      content: ''
    });
  };

  if (!isArray) {
    return (
      <Space size={12} direction="vertical" className="rb:w-full rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-2 rb:py-1.5 rb:bg-white" data-editor-type={parentName === 'template' ? 'template' : undefined}>
        <Row>
          <Col span={12}>
            {title ?? t('workflow.answerDesc')}
          </Col>
        </Row>
        <Form.Item name={parentName} noStyle>
          <Editor enableJinja2={enableJinja2} placeholder={placeholder} options={processedOptions} />
        </Form.Item>
      </Space>
    );
  }

  return (
    <Form.List name={parentName}>
      {(fields, { add, remove }) => (
        <Space size={12} direction="vertical" className="rb:w-full">
          {fields.map(({ key, name, ...restField }) => {
            const fieldValue = Array.isArray(parentName) 
              ? parentName.reduce((obj, key) => obj?.[key], values)
              : values?.[parentName];
            
            const currentRole = (fieldValue?.[name]?.role || 'USER').toUpperCase();
            
            return (
              <Space key={key} size={12} direction="vertical" className="rb:w-full rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-2 rb:py-1.5 rb:bg-white">
                <Row>
                  <Col span={12}>
                    <Form.Item {...restField} name={[name, 'role']} noStyle>
                      {currentRole === 'SYSTEM' ? (
                        <Input disabled />
                      ) : (
                        <Select
                          options={roleOptions}
                          disabled={currentRole === 'SYSTEM'}
                        />
                      )}
                    </Form.Item>
                  </Col>
                  {currentRole !== 'SYSTEM' && (
                    <Col span={12}>
                      <div className="rb:h-full rb:flex rb:justify-end rb:items-center">
                        <MinusCircleOutlined onClick={() => remove(name)} />
                      </div>
                    </Col>
                  )}
                </Row>
                <Form.Item {...restField} name={[name, 'content']} noStyle>
                  <Editor enableJinja2={enableJinja2} placeholder={placeholder} options={processedOptions} />
                </Form.Item>
              </Space>
            );
          })}
          <Form.Item>
            <Button type="dashed" onClick={() => handleAdd(add)} block>
              +{t('workflow.addMessage')}
            </Button>
          </Form.Item>
        </Space>
      )}
    </Form.List>
  );
};

export default MessageEditor;