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

import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type Key } from 'react';
import { Form, Select, Input, InputNumber } from 'antd';
import type { DefaultOptionType } from 'antd/es/select'
import { useTranslation } from 'react-i18next';

import type { SubAgentModalRef, SubAgentItem } from '../types'
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect';
import { getApplicationListUrl, getReleaseList } from '@/api/application';
import type { Release } from '@/views/ApplicationConfig/types/release'

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
  const [form] = Form.useForm<SubAgentItem>();
  const [loading, setLoading] = useState(false)
  const [editVo, setEditVo] = useState<SubAgentItem>()
  const appId = Form.useWatch('agent_id', form)
  const releasePolicy = Form.useWatch('release_policy', form) ?? 'current'
  const [referencedReleases, setReferencedReleases] = useState<Release[]>([])
  const [referencedReleasesLoading, setReferencedReleasesLoading] = useState(false)
  const releaseRequestRef = useRef(0)

  useEffect(() => {
    const requestId = ++releaseRequestRef.current

    if (!appId) {
      setReferencedReleases([])
      setReferencedReleasesLoading(false)
      return
    }

    setReferencedReleases([])
    setReferencedReleasesLoading(true)
    getReleaseList(appId)
      .then(response => {
        if (requestId !== releaseRequestRef.current) return

        const releaseList = Array.isArray(response)
          ? response
          : (response as { items?: Release[] } | undefined)?.items ?? []
        setReferencedReleases(releaseList)

        const selectedReleaseId = form.getFieldValue('release_id')
        if (
          form.getFieldValue('release_policy') === 'pinned'
          && selectedReleaseId
          && !releaseList.some(item => item.id === selectedReleaseId)
        ) {
          form.setFieldValue('release_id', undefined)
        }
      })
      .catch(() => {
        if (requestId === releaseRequestRef.current) {
          setReferencedReleases([])
        }
      })
      .finally(() => {
        if (requestId === releaseRequestRef.current) {
          setReferencedReleasesLoading(false)
        }
      })

    return () => {
      if (requestId === releaseRequestRef.current) {
        releaseRequestRef.current += 1
      }
    }
  }, [appId, form])

  /** Close modal and reset form */
  const handleClose = () => {
    releaseRequestRef.current += 1
    setVisible(false);
    form.resetFields();
    setEditVo(undefined)
    setReferencedReleases([])
    setReferencedReleasesLoading(false)
    setLoading(false)
  };

  /** Open modal with optional agent data */
  const handleOpen = (agent?: SubAgentItem) => {
    form.setFieldsValue({
      capabilities: [],
      priority: 1,
      release_policy: 'current',
      release_id: null,
      ...agent,
    })
    setEditVo(agent)
    setVisible(true);
  };

  /** Save sub-agent configuration */
  const handleSave = () => {
    setLoading(true)
    form.validateFields()
      .then(formValues => {
        const policy = formValues.release_policy ?? 'current'
        refresh({
          ...formValues,
          release_policy: policy,
          release_id: policy === 'pinned' ? formValues.release_id : null,
          is_active: true,
        })
        handleClose()
      })
      .finally(() => {
        setLoading(false)
      })
  }

  /** Handle agent selection change */
  const handleChange = (_value: Key, option?: DefaultOptionType | DefaultOptionType[]) => {
    form.setFieldValue('release_id', undefined)
    if (option && !Array.isArray(option)) {
      form.setFieldValue('name', option.children)
    }
  }

  const handlePolicyChange = (policy: SubAgentItem['release_policy']) => {
    if (policy !== releasePolicy) {
      form.setFieldValue('release_id', undefined)
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
            placeholder={t('common.pleaseEnter')}
            className="rb:w-full!"
          />
        </FormItem>

        <Form.Item
          name="release_policy"
          label={t('workflow.config.agent.releasePolicy')}
          rules={[
            { required: true, message: t('common.pleaseSelect') },
          ]}
        >
          <Select
            options={[
              { label: t('workflow.config.agent.currentRelease'), value: 'current' },
              { label: t('workflow.config.agent.pinnedRelease'), value: 'pinned' },
            ]}
            className="rb:w-full"
            onChange={handlePolicyChange}
          />
        </Form.Item>
        {releasePolicy === 'pinned' && (
          <Form.Item
            name="release_id"
            label={t('workflow.config.agent.releaseVersion')}
            rules={[
              { required: true, message: t('common.pleaseSelect') },
            ]}
          >
            <Select
              allowClear
              loading={referencedReleasesLoading}
              disabled={!appId}
              options={referencedReleases.map(item => ({
                label: item.version_name || item.name || (item.version ? `v${item.version}` : item.id),
                value: item.id,
              }))}
              placeholder={t('workflow.config.agent.releaseVersionPlaceholder')}
              className="rb:w-full"
            />
          </Form.Item>
        )}

        <FormItem
          name="priority"
          label={t('application.priority')}
          rules={[
            { required: true, message: t('common.pleaseEnter') },
          ]}
        >
          <InputNumber
            placeholder={t('common.pleaseEnter')}
            min={1}
            max={100}
            precision={0}
            step={1}
            className="rb:w-full!"
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default SubAgentModal;
