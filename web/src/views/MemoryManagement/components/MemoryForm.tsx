import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { MemoryFormData, Memory, MemoryFormRef } from '../types';
import RbModal from '@/components/RbModal'
import { createMemoryConfig, updateMemoryConfig } from '@/api/memory'
import { getOntologyScenesUrl } from '@/api/ontology'
import CustomSelect from '@/components/CustomSelect';

const FormItem = Form.Item;

interface MemoryFormProps {
  refresh: () => void;
}

const MemoryForm = forwardRef<MemoryFormRef, MemoryFormProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [editingMemory, setEditingMemory] = useState<Memory | null>(null);
  const [form] = Form.useForm<MemoryFormData>();
  const [loading, setLoading] = useState(false);

  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    setEditingMemory(null);
    form.resetFields();
    setLoading(false);
  };

  const handleOpen = (memory?: Memory | null) => {
    if (memory) {
      setEditingMemory(memory);
      // 设置表单值
      form.setFieldsValue({
        config_name: memory.config_name,
        config_desc: memory.config_desc,
        scene_id: memory.scene_id
      });
    } else {
      form.resetFields();
    }
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true)
        const response = editingMemory?.config_id ? updateMemoryConfig({
          config_id: editingMemory.config_id,
          ...values
        }) :createMemoryConfig(values)
          response.then(() => {
            if (refresh) {
              refresh();
            }
            handleClose()
            message.success(editingMemory?.config_id ? t('common.updateSuccess') : t('common.createSuccess'))
          }).finally(() => {
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
      title={editingMemory ? t('memory.editConfiguration') : t('memory.createConfiguration')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="config_name"
          label={t('memory.configurationName')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.pleaseEnter')} />
        </FormItem>
        
        <FormItem
          name="config_desc"
          label={t('memory.desc')}
        >
          <Input.TextArea placeholder={t('common.pleaseEnter')} />
        </FormItem>

        <Form.Item
          name="scene_id"
          label={t('memory.scene_id')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <CustomSelect
            placeholder={t('common.pleaseSelect')}
            url={getOntologyScenesUrl}
            params={{ pagesize: 100, page: 1 }}
            hasAll={false}
            valueKey='scene_id'
            labelKey="scene_name"
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default MemoryForm;