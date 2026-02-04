/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:28:51 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:28:51 
 */
/**
 * Sub-Agent Modal
 * Allows adding or editing sub-agents in multi-agent cluster configuration
 */

import { forwardRef, useImperativeHandle, useState, type Key } from 'react';
import { Form, Select, Input } from 'antd';
import type { DefaultOptionType } from 'antd/es/select'
import { useTranslation } from 'react-i18next';

import type { SubAgentModalRef, SubAgentItem } from '../types'
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect';
import { getApplicationListUrl } from '@/api/application';

const FormItem = Form.Item;

/**
 * Component props
 */
interface SubAgentModalProps {
  /** Callback to update sub-agent */
  refresh: (agent: SubAgentItem) => void;
}

/**
 * Modal for managing sub-agents
 */
const SubAgentModal = forwardRef<SubAgentModalRef, SubAgentModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<SubAgentItem>()
  const values = Form.useWatch([], form)

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /** Open modal with optional agent data */
  const handleOpen = (agent?: SubAgentItem) => {
    setVisible(true);
    form.setFieldsValue(agent)
    setEditVo(agent)
  };
  /** Save sub-agent configuration */
  const handleSave = () => {
    form.validateFields().then(() => {
      setLoading(false)
      refresh({
        ...values,
        is_active: true
      })
      handleClose()
    })
  }
  /** Handle agent selection change */
  const handleChange = (value: Key, option?: DefaultOptionType | DefaultOptionType[] | undefined) => {
    console.log(value, option)
    if (option && !Array.isArray(option)) {
      form.setFieldsValue({ name: option.children })
    }
  }

  /** Expose methods to parent component */
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
        {/* Agent name */}
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
        {/* Description */}
        <FormItem
          name="role"
          label={t('application.description')}
        >
          <Input.TextArea placeholder={t('common.pleaseEnter')} />
        </FormItem>
        {/* Keywords */}
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