import React from 'react';
import { useTranslation } from 'react-i18next'
import { Button, Form, Input, Divider } from 'antd';
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
            <div className="rb:flex rb:items-center rb:justify-between rb:mb-2">
              <div className="rb:text-[12px] rb:font-medium rb:leading-4.5">
                {t('workflow.config.jinja-render.mapping')}
              </div>

              <Button
                onClick={() => add()}
                className="rb:py-0! rb:px-1! rb:text-[12px]!"
                size="small"
              >
                + {t('workflow.config.addVariable')}
              </Button>
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
                    className="rb:w-24!"
                  />
                </Form.Item>
                <Form.Item
                  {...restField}
                  name={[name, 'value']}
                  noStyle
                >
                  <VariableSelect
                    placeholder={t('common.pleaseSelect')}
                    options={options}
                    popupMatchSelectWidth={false}
                    size="small"
                    className="rb:w-39!"
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
      <Divider />
    </>
  )
};

export default MappingList;