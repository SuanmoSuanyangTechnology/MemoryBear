import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, Checkbox, InputNumber, Button, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { InnerToolModalRef, ToolItem, InnerConfigItem, InnerToolItem } from '../types'
import RbModal from '@/components/RbModal'
import { InnerConfigData } from '../constant'
import RbAlert from '@/components/RbAlert';
import { updateTool, testConnection } from '@/api/tools'

const FormItem = Form.Item;

interface InnerToolModalProps {
  refreshTable: () => void;
}

const InnerToolModal = forwardRef<InnerToolModalRef, InnerToolModalProps>(({
  refreshTable
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<InnerToolItem>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<ToolItem>({} as ToolItem)
  const [config, setConfig] = useState<InnerConfigItem['config']>({});
  const search_type = Form.useWatch(['config', 'parameters', 'search_type'], form)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setConfig({})
  };

  const handleOpen = (data: ToolItem) => {
    setEditVo(data)
    const { config_data } = data
    form.setFieldsValue({
      config: {
        ...config_data,
        parameters: {
          search_type: 'web',
          ...(config_data as any).parameters
        },
      }
    })
    setConfig(InnerConfigData[config_data.tool_class].config)
    setVisible(true)
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        updateTool(editVo.id, {
          config: {
            ...editVo.config_data,
            ...values.config,
          }
        } as any)
          .then(() => {
            handleClose()
            message.success(t('common.saveSuccess'))
            refreshTable()
          })
      })
      .catch((err) => {
        console.log('err', err)
      });
  }
  const handleTestConnection = () => {
    testConnection(editVo.id)
      .then(() => {
        message.success(t('tool.testConnectionSuccess'));
      })
  };

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={`${editVo.name} ${t('tool.config')}`}
      open={visible}
      onCancel={handleClose}
      confirmLoading={loading}
      footer={[
        <Button onClick={handleClose}>{t('common.cancel')}</Button>,
        <Button onClick={handleTestConnection}>{t('tool.textLink')}</Button>,
        <Button type="primary" loading={loading} onClick={handleSave}>{t('common.save')}</Button>,
      ]}
    >
      {editVo?.config_data?.tool_class && config && <>
        <RbAlert className="rb:mb-3">
          <div>
            <div className="rb:text-[14px] rb:font-medium">{t('tool.configDesc')}</div>
            <div className="rb:mt-2">{t(`tool.${editVo?.config_data?.tool_class}_config_desc`)}</div>
            <div className="rb:font-medium">{t('tool.link')}: <Button size="small" type="link">{InnerConfigData[editVo?.config_data?.tool_class].link}</Button></div>
          </div>
        </RbAlert>
        <Form
          form={form}
          layout="vertical"
        >
          {Object.keys(config).map((key) => {
            const range = key === 'pagesize' && search_type ? config[key].range?.[search_type] ?? [] : [ config[key].min, config[key].max ]
            return (
              <FormItem
                key={key}
                label={config[key].type === 'checkbox' ? null : t(`tool.${key}`)}
                name={config[key].name}
                extra={config[key].desc ? t(`tool.${config[key].desc}`, { count1: range[0], count2: range[1] }) : null}
                valuePropName={config[key].type === 'checkbox' ? 'checked' : 'value'}
                rules={config[key].rules ? config[key].rules.map(vo => ({
                  ...vo,
                  message: t(vo.message)
                })) : []}
              >
                {config[key].type === 'input'
                  ? <Input placeholder={t('common.inputPlaceholder', { title: t(`tool.${key}`) })} />
                  : config[key].type === 'number'
                  ? <InputNumber 
                    placeholder={t('common.pleaseEnter')} 
                      min={range[0]}
                      max={range[1]}
                    step={config[key].step} 
                    className="rb:w-full!" 
                  />
                  : config[key].type === 'checkbox'
                  ? <Checkbox>{t(`tool.${key}`)}</Checkbox>
                  : config[key].type === 'select' && config[key].options
                  ? <Select 
                      placeholder={t('common.pleaseSelect')}
                      options={config[key].options.map(vo => ({
                        ...vo,
                        label: t(`tool.${vo.label}`)
                      }))}
                  />
                  : null
                }
              </FormItem>
            )
          })}
      </Form>
      </>}
    </RbModal>
  );
});

export default InnerToolModal;