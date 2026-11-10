import { forwardRef, useImperativeHandle, useRef, useState } from 'react';
import { App, Button, Form, Space } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import { appAssetInstall, getAppAssetDetail } from '@/api/resourceCenter';
import { getWorkspacesUrl, switchWorkspace } from '@/api/workspaces';
import type { AppAssetDetail } from '../types';
import { ShareMode } from '../types';
import CustomSelect from '@/components/CustomSelect';
import Tag from '@/components/Tag'
import RbAlert from '@/components/RbAlert'

export interface AddToWorkspaceModalRef {
  handleOpen: (id: string) => void;
}

const AddToWorkspaceModal = forwardRef<AddToWorkspaceModalRef>(function AddToWorkspaceModal(_, ref) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [item, setItem] = useState<AppAssetDetail>();
  const [form] = Form.useForm<{ space: string }>();
  const request = useRef(0);
  const close = () => {
    request.current += 1;
    setOpen(false);
    form.resetFields();
  };
  useImperativeHandle(ref, () => ({
    handleOpen(id) {
      const token = ++request.current;
      setOpen(true);
      setItem(undefined);
      form.resetFields();
      Promise.all([getAppAssetDetail(id)])
        .then(([detail]) => {
          if (token !== request.current) return;
          setItem(detail);
        })
        .catch(() => {
          if (token === request.current) message.error(t('resourceCenter.failed'));
        });
    },
  }));
  const save = ({ space }: { space: string }) => {
    if (!item || saving) return;
    setSaving(true);
    appAssetInstall(item.id, { target_workspace_id: space })
      .then(() => {
        close();
        // Determine the message and target tab based on share_mode
        const isEditable = item.share_mode === ShareMode.Editable;
        const messageText = isEditable ? t('resourceCenter.addedToMyApps') : t('resourceCenter.addedToShared');
        const targetTab = isEditable ? 'apps' : 'sharing';
        
        // Show notification with "前往查看" link
        message.success({
          content: (
            <Space>
              <span>{messageText}</span>
              <a
                onClick={() => {
                  switchWorkspace(space)
                    .then(() => {
                      localStorage.removeItem('user')
                      localStorage.setItem('pendingAppTab', targetTab);
                      window.location.href = '/#/application';
                    })
                    .catch(() => message.error(t('resourceCenter.failed')));
                }}
                className="rb:text-[#155EEF] rb:cursor-pointer"
              >
                {t('resourceCenter.goToView')}
              </a>
            </Space>
          ),
          duration: 5,
        });
      })
      .catch(() => message.error(t('resourceCenter.failed')))
      .finally(() => setSaving(false));
  };
  return (
    <RbModal
      open={open}
      width={480}
      title={t('resourceCenter.add')}
      onCancel={saving ? undefined : close}
      closable={!saving}
      footer={
        <Button type="primary" loading={saving} onClick={() => form.submit()}>
          {t('resourceCenter.add')}
        </Button>
      }
    >
      <Form form={form} layout="vertical" onFinish={save} className="rb:mt-6">
        <Form.Item
          name="space"
          label={t('resourceCenter.space')}
          rules={[{ required: true, message: t('resourceCenter.selectSpace') }]}
        >
          <CustomSelect
            url={getWorkspacesUrl}
            hasAll={false}
            showSearch={true}
            format={(list) => list.map(item => ({
              value: item.id,
              label: <Space>{item.name}<Tag color={item.storage_type === 'rag' ? 'processing' : 'warning'}>{t(`space.${item.storage_type || 'neo4j'}`)}</Tag></Space>
            }))}
          />
        </Form.Item>
      </Form>
      {item?.source_app_name &&
        <div className="rb:bg-gray-100 rb:p-3 rb:rounded-lg rb:mb-4">
          {t('resourceCenter.addToWorkspaceDesc', { name: item?.source_app_name })}
        </div>
      }
      <RbAlert color="orange" className="rb:mb-4">
        {t('resourceCenter.knowledgeBaseWarning')}
      </RbAlert>
    </RbModal>
  );
});

export default AddToWorkspaceModal;
