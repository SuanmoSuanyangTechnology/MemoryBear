import { forwardRef, useImperativeHandle, useState, type Key } from 'react';
import { Form, Select, Input } from 'antd';
import type { DefaultOptionType } from 'antd/es/select'
import { useTranslation } from 'react-i18next';

import type { SubAgentModalRef, SubAgentItem } from '../types'
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect';
import { getApplicationListUrl } from '@/api/application';

const FormItem = Form.Item;

interface SubAgentModalProps {
  refresh: (agent: SubAgentItem) => void;
}

const SubAgentModal = forwardRef<SubAgentModalRef, SubAgentModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<SubAgentItem>()
  const values = Form.useWatch([], form)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = (agent?: SubAgentItem) => {
    setVisible(true);
    form.setFieldsValue(agent)
    setEditVo(agent)
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form.validateFields().then(() => {
      setLoading(false)
      refresh(values)
      handleClose()
    })
  }
  const handleChange = (value: Key, option?: DefaultOptionType | DefaultOptionType[] | undefined) => {
    console.log(value, option)
    if (option && !Array.isArray(option)) {
      form.setFieldsValue({ name: option.children })
    }
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t(`application.${editVo?.agent_id ? 'updateSubAgent' : 'addSubAgent'}`)}
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
        {/* Agent名称 */}
        <FormItem
          name="agent_id"
          label={t('application.agentName')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
          ]}
        >
          <CustomSelect
            url={getApplicationListUrl}
            params={{ pagesize: 100, status: 'active', type: 'agent' }}
            valueKey="id"
            labelKey="name"
            hasAll={false}
            optionFilterProp="search"
            showSearch={true}
            onChange={handleChange}
          />
        </FormItem>
        <FormItem name="name" hidden />
        {/* 描述 */}
        <FormItem
          name="role"
          label={t('application.description')}
        >
          <Input.TextArea placeholder={t('common.pleaseEnter')} />
        </FormItem>
        {/* 关键词 */}
        <FormItem
          name="capabilities"
          label={t('application.capabilities')}
        >
          <Select
            mode="tags"
            style={{ width: '100%' }}
            placeholder={t('common.pleaseEnter')}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default SubAgentModal;