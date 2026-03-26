import { type FC } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Switch, Flex } from 'antd'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import MessageEditor from '../MessageEditor'
import RbSlider from "@/components/RbSlider";

const MemoryConfig: FC<{ options: Suggestion[]; parentName: string; }> = ({
  options,
  parentName
}) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance();
  const values = Form.useWatch([], form) || {}

  console.log('MemoryConfig', values)
  
  const handleChangeEnable = (value: boolean) => {
    if (value) {
      form.setFieldsValue({
        memory: {
          ...form.getFieldValue(parentName),
          enable_window: false,
          window_size: 20,
          messages: "{{sys.message}}"
        }
      })
    }
  }

  return (
    <>
      <Form.Item layout="horizontal" name={[parentName, 'enable']} label={t('workflow.config.llm.memory')} className={values?.memory?.enable ? "rb:mb-2!" : undefined}>
        <Switch onChange={handleChangeEnable} />
      </Form.Item>
      {values?.memory?.enable && <>
        <Flex align="center" justify="space-between" className="rb:py-1.25! rb:px-2! rb:text-[12px] rb:leading-4.5 rb:bg-[#F6F6F6] rb:rounded-lg rb:mb-2!">
          {t('workflow.config.llm.memory')}
          <span>{t('workflow.config.llm.inner')}</span>
        </Flex>
        <Form.Item layout="horizontal" name={[parentName, 'messages']} className="rb:mb-2!">
          <MessageEditor
            title="USER"
            isArray={false}
            parentName={[parentName, 'messages']}
            options={options}
            size="small"
          />
        </Form.Item>
        <div className="rb-border rb:rounded-lg rb:p-2 rb:mb-4">
          <Form.Item layout="horizontal" name={[parentName, 'enable_window']} label={t('workflow.config.llm.enable_window')} className="rb:mb-2!">
            <Switch />
          </Form.Item>
          <Form.Item layout="horizontal" name={[parentName, 'window_size']} noStyle>
            <RbSlider 
              min={1} 
              max={100} 
              step={1} 
              size="small"
              isInput={true}
              disabled={!values?.memory?.enable_window}
            />
          </Form.Item>
        </div>
      </>}
    </>
  );
};
export default MemoryConfig;