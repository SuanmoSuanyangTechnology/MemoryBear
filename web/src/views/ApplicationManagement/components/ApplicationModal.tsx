import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';
import RadioGroupCard from '@/components/RadioGroupCard'
import AgentIcon from '@/assets/images/application/agent.svg'
import ClusterIcon from '@/assets/images/application/cluster.svg'
import WorkflowIcon from '@/assets/images/application/workflow.svg'

import type { ApplicationModalData, ApplicationModalRef, Application } from '../types'
import RbModal from '@/components/RbModal'
import { addApplication, updateApplication } from '@/api/application'

const FormItem = Form.Item;

interface ApplicationModalProps {
  refresh: () => void;
}

const types = [
  'agent',
  'multi_agent',
  'workflow'
]
const typeIcons: Record<string, string> = {
  agent: AgentIcon,
  multi_agent: ClusterIcon,
  workflow: WorkflowIcon
}

const ApplicationModal = forwardRef<ApplicationModalRef, ApplicationModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ApplicationModalData>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<Application | null>(null)

  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditVo(null)
  };

  const handleOpen = (application?: Application) => {
    if (application) {
      setEditVo(application || null)
      form.setFieldsValue({
        name: application.name,
        type: application.type,
        description: application.description,
      })
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

        const response = editVo?.id ? updateApplication(editVo.id, {
          ...editVo,
          ...values,
        }) : addApplication(values)
        response.then(() => {
          refresh()
          handleClose()
        })
        .finally(() => {
          setLoading(false)
        });
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
      title={t(`application.${editVo?.id ? 'editApplication' : 'createApplication'}`)}
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
          name="name"
          label={t('application.applicationName')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        <FormItem
          name="description"
          label={t('application.description')}
        >
          <Input.TextArea placeholder={t('common.enter')} />
        </FormItem>
        
        <FormItem
          name="type"
          label={t('application.applicationType')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <RadioGroupCard
            options={types.map((type) => ({
              value: type,
              label: t(`application.${type}`),
              labelDesc: t(`application.${type}Desc`),
              icon: typeIcons[type],
            }))}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ApplicationModal;