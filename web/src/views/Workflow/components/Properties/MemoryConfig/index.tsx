import { type FC } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Row, Col, Divider, Switch, Slider } from 'antd'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import MessageEditor from '../MessageEditor'

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
      {values?.memory?.enable && <>
        <div className="rb:flex rb:items-center rb:justify-between rb:py-1.5 rb:px-2 rb:text-[12px] rb:bg-[#F6F8FC] rb:rounded-md rb:mb-2">
          {t('workflow.config.llm.memory')}
          <span>{t('workflow.config.llm.inner')}</span>
        </div>
        <Form.Item layout="horizontal" name={[parentName, 'messages']}>
          <MessageEditor
            title="USER"
            isArray={false}
            parentName={[parentName, 'messages']}
            options={options}
            size="small"
          />
        </Form.Item>

        <Divider />
      </>}
      <Form.Item layout="horizontal" name={[parentName, 'enable']} label={t('workflow.config.llm.memory')}>
        <Switch onChange={handleChangeEnable} />
      </Form.Item>
      {values?.memory?.enable && <>
        <Row className="rb:mb-3">
          <Col span={10}>
            <Form.Item layout="horizontal" name={[parentName, 'enable_window']} noStyle>
              <Switch />
            </Form.Item>
            <span className="rb:ml-2">{t('workflow.config.llm.enable_window')}</span>
          </Col>
          <Col span={14}>
            <Form.Item layout="horizontal" name={[parentName, 'window_size']} noStyle>
              <Slider min={1} max={100} step={1} className="rb:my-0!" disabled={!values?.memory?.enable_window} />
            </Form.Item>
          </Col>
        </Row>
      </>}
    </>
  );
};
export default MemoryConfig;