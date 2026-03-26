import { type FC, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next'
import { Button, Form, Input, Space, Flex } from 'antd';

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import VariableSelect from '../VariableSelect'

interface MappingListProps {
  label: string;
  name: string;
  options: Suggestion[];
  extra?: ReactNode;
  valueKey?: string;
}
const MappingList: FC<MappingListProps> = ({ label, name, options, extra, valueKey = 'value' }) => {
  const { t } = useTranslation()
  return (
    <>
      <Form.List name={name}>
        {(fields, { add, remove }) => (
          <>
            <Flex align="center" justify="space-between" className="rb:mb-2!">
              <div className="rb:text-[12px] rb:font-medium rb:leading-4.5">
                {label}
              </div>

              <Space size={8}>
                {extra}
                <Button
                  onClick={() => add()}
                  className="rb:py-0! rb:px-1! rb:h-4.5! rb:rounded-sm! rb:text-[12px]!"
                  size="small"
                >
                  + {t('workflow.config.addVariable')}
                </Button>
              </Space>
            </Flex>
            <Flex gap={8} vertical>
              {fields.map(({ key, name, ...restField }) => (
                <Flex key={key} align="center" gap={4}>
                  <Form.Item
                    {...restField}
                    name={[name, 'name']}
                    noStyle
                  >
                    <Input 
                      placeholder={t('common.pleaseEnter')} 
                      size="small"
                      className="rb:w-27!"
                    />
                  </Form.Item>
                  <Form.Item
                    {...restField}
                    name={[name, valueKey]}
                    noStyle
                  >
                    <VariableSelect
                      placeholder={t('common.pleaseSelect')}
                      options={options}
                      popupMatchSelectWidth={false}
                      size="small"
                      className="rb:w-51!"
                    />
                  </Form.Item>
                  <div
                    className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                    onClick={() => remove(name)}
                  ></div>
                </Flex>
              ))}
            </Flex>
          </>
        )}
      </Form.List>
    </>
  )
};

export default MappingList;