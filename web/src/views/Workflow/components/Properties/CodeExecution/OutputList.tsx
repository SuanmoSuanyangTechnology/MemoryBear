import { type FC, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next'
import { Button, Form, Input, Divider, Space, Select } from 'antd';

interface OutputListProps {
  label: string;
  name: string;
  extra?: ReactNode;
}

const types = [
  'string',
  'number',
  'boolean',
  'array[string]',
  'array[number]',
  'array[boolean]',
  'array[object]',
  'object'
]
const OutputList: FC<OutputListProps> = ({ label, name, extra }) => {
  const { t } = useTranslation()
  return (
    <>
      <Form.List name={name}>
        {(fields, { add, remove }) => (
          <>
            <div className="rb:flex rb:items-center rb:justify-between rb:mb-2">
              <div className="rb:text-[12px] rb:font-medium rb:leading-4.5">
                {label}
              </div>

              <Space size={8}>
                {extra}
                <Button
                  onClick={() => add({ type: 'string' })}
                  className="rb:py-0! rb:px-1! rb:text-[12px]!"
                  size="small"
                >
                  + {t('workflow.config.addVariable')}
                </Button>
              </Space>
            </div>
            {fields.map(({ key, name, ...restField }) => (
              <div key={key} className="rb:flex rb:items-center rb:gap-1 rb:mb-2">
                <Form.Item
                  {...restField}
                  name={[name, 'name']}
                  noStyle
                >
                  <Input 
                    placeholder={t('common.pleaseEnter')} 
                    size="small"
                    className="rb:w-45!"
                  />
                </Form.Item>
                <Form.Item
                  {...restField}
                  name={[name, 'type']}
                  noStyle
                >
                  <Select
                    placeholder={t('common.pleaseSelect')} 
                    options={types.map(key => ({
                      value: key,
                      label: t(`workflow.config.parameter-extractor.${key}`),
                    }))}
                    size="small"
                    popupMatchSelectWidth={false}
                    className="rb:w-22!"
                  />
                </Form.Item>
                <div
                  className="rb:ml-1 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                  onClick={() => remove(name)}
                ></div>
              </div>
            ))}
          </>
        )}
      </Form.List>
    </>
  )
};

export default OutputList;