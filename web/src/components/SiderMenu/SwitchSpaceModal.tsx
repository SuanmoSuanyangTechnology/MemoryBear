/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-22 18:50:14 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-04-22 18:50:14 
 */
/**
 * SwitchSpaceModal Component
 * 
 * A modal for switching the current workspace.
 * Displays a dropdown to select a workspace and reloads the page upon confirmation.
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, App, Space } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal'
import { switchWorkspace, getWorkspacesUrl } from '@/api/workspaces'
import CustomSelect from '@/components/CustomSelect';
import Tag from '@/components/Tag'
import { useUser } from '@/store/user';

const FormItem = Form.Item;

export interface SwitchSpaceModalRef {
  handleOpen: () => void;
}

const SwitchSpaceModal = forwardRef<SwitchSpaceModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{ space_id: string }>();
  const [loading, setLoading] = useState(false)
  const { user } = useUser()

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /** Open modal */
  const handleOpen = () => {
    form.resetFields();
    setVisible(true);
    form.setFieldsValue({ space_id: user?.current_workspace_id })
  };
  /** Handle save/next button click - proceed to next step or submit email change */
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        if (user?.current_workspace_id === values.space_id) {
          handleClose()
          return
        }
        setLoading(true)
        switchWorkspace(values.space_id)
          .then(res => {
            if (res) {
              message.success(t('common.operateSuccess'));
              localStorage.removeItem('user')
              window.location.reload()
            }
          })
          .finally(() => setLoading(false))
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbModal
      title={t('common.switchSpace')}
      open={visible}
      onCancel={handleClose}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="space_id"
          label={t('space.spaceName')}
          rules={[
            { required: true, message: t('common.pleaseSelect') },
          ]}
        >
          <CustomSelect
            url={getWorkspacesUrl}
            hasAll={false}
            format={(list) => list.map(item => ({
              value: item.id,
              label: <Space>{item.name}<Tag color={item.storage_type === 'rag' ? 'processing' : 'warning'}>{t(`space.${item.storage_type || 'neo4j'}`)}</Tag></Space>
            }))}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default SwitchSpaceModal;