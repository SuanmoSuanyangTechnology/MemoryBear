import { type FC, type ReactNode, useMemo } from 'react';
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { Input, Form, Button, Row, Col, Select, type FormListOperation, Flex } from 'antd';

import Editor, { type LexicalEditorProps } from '../Editor'
import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'

interface MessageEditor {
  options?: Suggestion[];
  title?: string | ReactNode;
  titleVariant?: 'outlined' | 'borderless';
  isArray?: boolean;
  parentName?: string | string[];
  label?: string;
  placeholder?: string;
  value?: string;
  language?: LexicalEditorProps['language'];
  onChange?: (value?: string) => void;
  size?: 'small' | 'default';
  className?: string;
}
const roleOptions = [
  // { label: 'SYSTEM', value: 'SYSTEM' },
  { label: 'USER', value: 'USER' },
  { label: 'ASSISTANT', value: 'ASSISTANT' },
]
const MessageEditor: FC<MessageEditor> = ({
  title,
  titleVariant = 'outlined',
  isArray = true,
  parentName = 'messages',
  placeholder,
  options = [],
  language,
  size = 'default',
  className
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
      <Flex gap={8} vertical className={clsx("rb-border rb:rounded-lg rb:px-2! rb:py-1.5!", className)} data-editor-type={parentName === 'template' ? 'template' : undefined}>
        <Row>
          <Col span={12}>
            {typeof title === 'string'
            ? <div className={clsx("rb:text-[12px] rb:text-[#212332] rb:font-medium rb:leading-4", {
              'rb:bg-[#F6F6F6] rb-border rb:rounded-md rb:px-2 rb:py-1': titleVariant === 'outlined'
            })}>{title ?? t('workflow.answerDesc')}</div>
            : title}
          </Col>
        </Row>
        <Form.Item name={parentName} noStyle>
          <Editor size={size} language={language} placeholder={placeholder} options={processedOptions} />
        </Form.Item>
      </Flex>
    );
  }

  return (
    <Form.List name={parentName}>
      {(fields, { add, remove }) => (
        <Flex gap={8} vertical>
          {fields.map(({ key, name, ...restField }) => {
            const fieldValue = Array.isArray(parentName) 
              ? parentName.reduce((obj, key) => obj?.[key], values)
              : values?.[parentName];
            
            const currentRole = (fieldValue?.[name]?.role || 'USER').toUpperCase();
            
            return (
              <Flex key={key} gap={8} vertical className="rb-border rb:rounded-md rb:p-2!">
                <Row>
                  <Col span={12}>
                    <Form.Item {...restField} name={[name, 'role']} noStyle>
                      {currentRole === 'SYSTEM' ? (
                        <Input disabled className="rb:font-medium! rb:text-[#212332]!" />
                      ) : (
                        <Select
                          options={roleOptions}
                          disabled={currentRole === 'SYSTEM'}
                          className="rb:font-medium!"
                        />
                      )}
                    </Form.Item>
                  </Col>
                  {currentRole !== 'SYSTEM' && (
                    <Col span={12}>
                      <Flex align="center" justify="end" className="rb:h-full">
                        <div
                          className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/delete_cycle.svg')]"
                          onClick={() => remove(name)}
                        ></div>
                      </Flex>
                    </Col>
                  )}
                </Row>
                <Form.Item {...restField} name={[name, 'content']} noStyle>
                  <Editor size={size} language={language} placeholder={placeholder} options={processedOptions} />
                </Form.Item>
              </Flex>
            );
          })}
          <Form.Item noStyle>
            <Button type="dashed" size="middle" className="rb:text-[12px]!" onClick={() => handleAdd(add)} block>
              + {t('workflow.addMessage')}
            </Button>
          </Form.Item>
        </Flex>
      )}
    </Form.List>
  );
};

export default MessageEditor;