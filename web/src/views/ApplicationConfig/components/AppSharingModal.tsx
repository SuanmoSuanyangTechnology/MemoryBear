/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-13 17:19:13 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-18 10:47:17
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Checkbox, App, Form } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import { appSharing, getAppShares } from '@/api/application';
import { formatDateTime } from '@/utils/format';
import type { AppSharingModalRef, Release } from '../types';
import type { SpaceItem } from '@/views/KnowledgeBase/types';
import { getWorkspaces } from '@/api/workspaces';
import RadioGroupCard from '@/components/RadioGroupCard';

/** Props for the AppSharingModal component */
interface AppSharingModalProps {
  /** ID of the application being shared */
  appId: string;
  /** The release version to share */
  version: Release | null;
}

const AppSharingModal = forwardRef<AppSharingModalRef, AppSharingModalProps>(({ appId, version }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  // All workspaces available to share with (excluding the current one)
  const [spaceList, setSpaceList] = useState<SpaceItem[]>([]);
  // IDs of workspaces that already have access to this app
  const [sharedIds, setSharedIds] = useState<string[]>([]);

  const [form] = Form.useForm<{ target_workspace_ids: string[]; permission: 'readonly' | 'editable' }>();
  // Reactively track the currently selected workspace IDs in the form
  const selectedIds: string[] = Form.useWatch('target_workspace_ids', form) ?? [];

  /**
   * Fetch workspaces and existing share records in parallel,
   * sort already-shared spaces to the top, then open the modal.
   * Shows a warning if the user has no shareable workspaces.
   */
  const handleOpen = () => {
    Promise.all([getWorkspaces({ include_current: false }), getAppShares(appId)]).then(([spaces, shared]) => {
      // Normalise the shared workspace ID field across different API response shapes
      const ids = ((shared as any[]) || []).map((s: any) => s.workspace_id || s.target_workspace_id || s.id);
      // Sort: already-shared workspaces appear first
      const sorted = (spaces as SpaceItem[]).sort((a, b) =>
        ids.includes(b.id) ? 1 : ids.includes(a.id) ? -1 : 0
      );
      setSpaceList(sorted);
      setSharedIds(ids);

      if (sorted.length > 0) {
        setVisible(true);
      } else {
        message.warning(t('application.noShareAuth'));
      }
    });
  };

  /** Close the modal and reset form fields */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  // Expose open/close handlers to the parent via ref
  useImperativeHandle(ref, () => ({ handleOpen, handleClose }));

  /**
   * Toggle a workspace in the selected list.
   * Already-shared workspaces are read-only and cannot be toggled.
   */
  const handleToggle = (id: string, isShared: boolean) => {
    if (isShared) return;
    const prev = form.getFieldValue('target_workspace_ids') as string[] ?? [];
    form.setFieldValue(
      'target_workspace_ids',
      prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]
    );
  };

  /** Validate the form then submit the sharing request */
  const handleConfirm = () => {
    form.validateFields().then(values => {
      setLoading(true);
      appSharing(appId, values)
        .then(() => {
          message.success(t('common.operateSuccess'));
          handleClose();
        })
        .finally(() => setLoading(false));
    });
  };

  // Normalise the version label to always start with "v"
  const versionLabel = version?.version_name
    ? (version.version_name[0].toLowerCase() === 'v' ? version.version_name : `v${version.version_name}`)
    : `v${version?.version}`;

  return (
    <RbModal
      title={t('application.sharingApp')}
      open={visible}
      onCancel={handleClose}
      okText={<>{t('application.confirmSharing')}({selectedIds.length})</>}
      onOk={handleConfirm}
      confirmLoading={loading}
      width={600}
    >
      <Form form={form} layout="vertical" initialValues={{ target_workspace_ids: [], permission: 'readonly' }}>
        {/* Version info: displays version number, release time and publisher */}
        <div className="rb:rounded-lg rb:border rb:border-[#EBEBEB] rb:bg-[#FBFDFF] rb:p-4 rb:mb-4">
          <div className="rb:text-sm rb:font-medium rb:mb-3">{t('application.VersionInformation')}</div>
          <div className="rb:grid rb:grid-cols-3 rb:gap-4 rb:text-sm">
            <div>
              <div className="rb:text-[#5B6167] rb:mb-1">{t('application.versionList').replace('列表', '号')}</div>
              <div className="rb:font-medium">{versionLabel}</div>
            </div>
            <div>
              <div className="rb:text-[#5B6167] rb:mb-1">{t('application.releaseTime')}</div>
              <div className="rb:font-medium">{formatDateTime(version?.published_at || 0, 'YYYY-MM-DD HH:mm:ss')}</div>
            </div>
            <div>
              <div className="rb:text-[#5B6167] rb:mb-1">{t('application.publisher')}</div>
              <div className="rb:font-medium">{version?.publisher_name}</div>
            </div>
          </div>
        </div>

        {/* Target space: scrollable list of workspaces with checkbox selection */}
        <Form.Item
          name="target_workspace_ids"
          label={t('application.selectTargetSpace')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <div className="rb:rounded-lg rb:border rb:border-[#EBEBEB] rb:divide-y rb:divide-[#EBEBEB] rb:max-h-50 rb:overflow-y-auto">
            {spaceList.map(space => {
              const isShared = sharedIds.includes(space.id);
              return (
                <div key={space.id} className="rb:flex rb:items-center rb:gap-2 rb:px-4 rb:py-3 rb:cursor-pointer" onClick={() => handleToggle(space.id, isShared)}>
                  <Checkbox
                    checked={isShared || selectedIds.includes(space.id)}
                    disabled={isShared} // already-shared workspaces cannot be unselected
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => handleToggle(space.id, isShared)}
                  />
                  <span className="rb:flex-1 rb:text-sm">{space.name}</span>
                  {/* Badge shown when the app is already shared with this workspace */}
                  {isShared && (
                    <span className="rb:text-xs rb:text-[#5B6167]">{t('application.alreadyShared')}</span>
                  )}
                </div>
              );
            })}
          </div>
        </Form.Item>

        {/* Permission mode: readonly (use only) or editable (full copy) */}
        <Form.Item
          name="permission"
          label={t('application.permissionMode')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
          className="rb:mb-0!"
        >
          <RadioGroupCard
            options={['readonly', 'editable'].map((type) => ({
              value: type,
              label: t(`application.${type}Mode`),
              labelDesc: t(`application.${type}ModeDesc`),
            }))}
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default AppSharingModal;
