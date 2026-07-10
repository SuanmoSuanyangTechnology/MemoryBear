import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, Checkbox, InputNumber, Switch, Row, Col } from 'antd';
import { useTranslation } from 'react-i18next';

import type { DatabaseToolModalRef, ToolItem, ExecuteData, ConfigItem } from '../types'
import RbModal from '@/components/RbModal'
import { execute } from '@/api/tools'
import CodeBlock from '@/components/Markdown/CodeBlock';

const FormItem = Form.Item;

const configs: Record<string, ConfigItem> = {
  driver: {
    name: ['parameters', 'driver'],
    type: 'select',
    options: [
      { label: 'postgresql', value: 'postgresql' },
    ],
    defaultValue: 'postgresql',
    rules: [
      { required: true, message: 'common.pleaseSelect' }
    ]
  },
  host: {
    name: ['parameters', 'host'],
    type: 'input',
    desc: 'DatabaseTool_host_desc',
    rules: [
      { required: true, message: 'common.pleaseEnter' }
    ]
  },
  port: {
    name: ['parameters', 'port'],
    type: 'number',
    min: 0,
    step: 1,
    desc: 'DatabaseTool_port_desc',
    rules: [
      { required: true, message: 'common.pleaseEnter' }
    ]
  },
  user: {
    name: ['parameters', 'user'],
    type: 'input',
    desc: 'DatabaseTool_user_desc',
    rules: [
      { required: true, message: 'common.pleaseEnter' }
    ]
  },
  password: {
    name: ['parameters', 'password'],
    type: 'password',
    desc: 'DatabaseTool_password_desc',
    rules: [
      { required: true, message: 'common.pleaseEnter' }
    ]
  },
  connect_timeout: {
    name: ['parameters', 'connect_timeout'],
    type: 'number',
    min: 0,
    max: 600,
    step: 1,
    desc: 'DatabaseTool_connect_timeout_desc',
  },
  database: {
    name: ['parameters', 'database'],
    type: 'input',
    desc: 'DatabaseTool_database_desc',
    rules: [
      { required: true, message: 'common.pleaseEnter' }
    ]
  },
  sql: {
    name: ['parameters', 'sql'],
    type: 'textarea',
    rules: [
      { required: true, message: 'common.pleaseEnter' }
    ]
  },
}

const DatabaseToolModal = forwardRef<DatabaseToolModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ExecuteData>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<ToolItem>({} as ToolItem)
  const [formatValue, setFormatValue] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setFormatValue(null)
    setError(null)
  };

  const handleOpen = (data: ToolItem) => {
    setEditVo(data)
    form.resetFields();
    setVisible(true)
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        execute({
          ...values,
          tool_id: editVo.id,
        })
          .then((res) => {
            setFormatValue(JSON.stringify(res || {}, null, 2))
            setError(null)
          })
          .catch((err) => {
            setFormatValue(null)
            setError(err?.response?.data?.error || err?.response?.data?.message || null)
          })
          .finally(() => {
            setLoading(false)
          })
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={editVo.name}
      open={visible}
      onCancel={handleClose}
      okText={t('tool.testLink')}
      onOk={handleSave}
      confirmLoading={loading}
      width={1000}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Row gutter={12} className="rb:overflow-hidden!">
          <Col span={12} className="rb:max-h-[calc(100vh-202px)]! rb:overflow-y-auto!">
            {Object.keys(configs).map((key) => {
              const range = [ configs[key].min, configs[key].max ]
              return (
                <FormItem
                  key={key}
                  label={configs[key].type === 'checkbox' ? null : t(`tool.${key}`)}
                  name={configs[key].name}
                  extra={configs[key].desc ? t(`tool.${configs[key].desc}`, { count1: range[0], count2: range[1] }) : null}
                  valuePropName={configs[key].type === 'checkbox' ? 'checked' : 'value'}
                  rules={configs[key].rules ? configs[key].rules.map(vo => ({
                    ...vo,
                    message: t(vo.message)
                  })) : []}
                  layout={configs[key].type === 'switch' ? 'horizontal' : 'vertical'}
                >
                  {configs[key].type === 'input'
                    ? <Input placeholder={t('common.inputPlaceholder', { title: t(`tool.${key}`) })} />
                    : configs[key].type === 'textarea'
                    ? <Input.TextArea placeholder={t('common.inputPlaceholder', { title: t(`tool.${key}`) })} />
                    : configs[key].type === 'number'
                    ? <InputNumber 
                      placeholder={t('common.pleaseEnter')} 
                      min={range[0]}
                      max={range[1]}
                      step={configs[key].step} 
                      className="rb:w-full!" 
                    />
                    : configs[key].type === 'checkbox'
                    ? <Checkbox>{t(`tool.${key}`)}</Checkbox>
                    : configs[key].type === 'select' && configs[key].options
                    ? <Select 
                        placeholder={t('common.pleaseSelect')}
                        options={configs[key].options.map(vo => ({
                          ...vo,
                          label: t(`tool.${vo.label}`)
                        }))}
                    />
                    : configs[key].type === 'switch'
                    ? <Switch />
                    : configs[key].type === 'password'
                    ? <Input.Password placeholder={t('common.pleaseEnter')} />
                    : null
                  }
                </FormItem>
              )
            })}
          </Col>
          <Col span={12}>
            <FormItem
              label={t('tool.outputResult')}
            >
              <div className="rb:h-[calc(100vh-248px)]! rb:overflow-y-auto!">
                {(typeof formatValue === "string" && formatValue) || (typeof error === "string" && error)
                  ? <CodeBlock value={formatValue || error || ''} background={error ? 'rgba(255,138,76,0.08)' : '#F0F3F8'} />
                  : <div className="rb:h-full rb:bg-[#F0F3F8] rb:text-[12px] rb:p-[16px_20px_16px_24px] rb:rounded-lg rb:text-[#A8A9AA]">{t('tool.noResult')}</div>
                }
              </div>
            </FormItem>
          </Col>
        </Row>
      </Form>
    </RbModal>
  );
});

export default DatabaseToolModal;