/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:34:09 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-06 16:01:16
 */
/**
 * Application Modal
 * Modal for creating and editing applications
 * Supports three application types: agent, multi_agent, and workflow
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import RadioGroupCard from '@/components/RadioGroupCard'
import AgentIcon from '@/assets/images/application/agent.svg'
import ClusterIcon from '@/assets/images/application/multi_agent.svg'
import WorkflowIcon from '@/assets/images/application/workflow.svg'
import PureWorkflowIcon from '@/assets/images/application/pure_workflow.svg'
import type { ApplicationModalData, ApplicationModalRef, Application } from '../types'
import RbModal from '@/components/RbModal'
import { addApplication, updateApplication } from '@/api/application'
import { stringRegExp } from '@/utils/validator';
import { getMemoryConfigList } from '@/api/memory'
import type { Memory } from '@/views/MemoryManagement/types'
import ActiveMemoryConfig from '@/components/ActiveMemoryConfig'

const FormItem = Form.Item;

/**
 * Component props
 */
interface ApplicationModalProps {
  /** Callback to refresh application list */
  refresh: () => void;
}

/**
 * Supported application types
 */
export const types = [
  'agent',
  'multi_agent',
  'workflow',
  'pure_workflow'
]
/**
 * Application type icon mapping
 */
const typeIcons: Record<string, string> = {
  agent: AgentIcon,
  multi_agent: ClusterIcon,
  workflow: WorkflowIcon,
  pure_workflow: PureWorkflowIcon,
}

/**
 * Modal for creating and editing applications
 */
const ApplicationModal = forwardRef<ApplicationModalRef, ApplicationModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ApplicationModalData>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<Application | null>(null)

  const values = Form.useWatch([], form);

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditVo(null)
  };

  /** Open modal with optional application data for editing */
  const handleOpen = (application?: Application) => {
    getActiveMemoryConfig()
    if (application) {
      setEditVo(application || null)
      form.setFieldsValue({
        name: application.name,
        type: application.type,
        description: application.description,
        tags: application.tags || [],
      })
    } else {
      form.resetFields();
    }
    setVisible(true);
  };
  /** Save application (create or update) */
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
  const [activeMemoryConfig, setActiveMemoryConfig] = useState<Memory | null>(null)
  const getActiveMemoryConfig = () => {
    getMemoryConfigList()
      .then((res) => {
        setActiveMemoryConfig((res as Memory[]).find(item => item.is_active) || null)
      })
      .catch(() => {
        setActiveMemoryConfig(null)
      })
  }

  /** Expose methods to parent component */
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
          rules={[
            { required: true, message: t('common.pleaseEnter') },
            { max: 50 },
            { pattern: stringRegExp, message: t('common.nameInvalid') },
          ]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        <FormItem
          name="description"
          label={t('application.description')}
          rules={[{ max: 500 }]}
        >
          <Input.TextArea placeholder={t('common.enter')} />
        </FormItem>
        <FormItem
          name="tags"
          label={t('application.tags')}
        >
          <Select mode="tags" placeholder={t('common.pleaseEnter')} />
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
            disabled={!!editVo?.id}
            block={true}
          />
        </FormItem>

        <>
          <div className="rb:font-medium">{t('application.memoryConfiguration')}</div>
          <div className="rb:text-[12px] rb:text-[#5B6167]">{t('application.memoryConfigurationDesc')}</div>

          <ActiveMemoryConfig
            activeMemoryConfig={activeMemoryConfig}
            variant="filled"
          />
        </>
      </Form>
    </RbModal>
  );
});

export default ApplicationModal;