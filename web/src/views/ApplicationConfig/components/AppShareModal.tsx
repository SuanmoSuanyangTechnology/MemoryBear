/**
 * App Share Modal
 * 将应用共享到其他工作空间，支持选择权限模式
 */
import { forwardRef, useImperativeHandle, useState, useMemo } from 'react';
import { App, Input } from 'antd';
import { useTranslation } from 'react-i18next';
import { PlayCircleFilled, CopyOutlined, SearchOutlined } from '@ant-design/icons';

import RbModal from '@/components/RbModal';
import { getSpaceList } from '@/api/knowledgeBase';
import { shareAppToWorkspace } from '@/api/application';
import type { AppShareModalRef } from '../types';

interface SpaceItem {
  id: string;
  name: string;
  icon?: string;
  is_active: boolean;
}

interface AppShareModalProps {
  appId: string;
  onSuccess?: () => void;
}

type Permission = 'readonly' | 'editable';

interface PermissionOption {
  value: Permission;
  icon: React.ReactNode;
  label: string;
  desc: string;
  iconBg: string;
}

const AppShareModal = forwardRef<AppShareModalRef, AppShareModalProps>(({ appId, onSuccess }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [spaceList, setSpaceList] = useState<SpaceItem[]>([]);
  const [selectedSpaceIds, setSelectedSpaceIds] = useState<string[]>([]);
  const [permission, setPermission] = useState<Permission>('readonly');
  const [searchKeyword, setSearchKeyword] = useState('');

  const permissionOptions: PermissionOption[] = [
    {
      value: 'readonly',
      icon: <PlayCircleFilled className="rb:text-[#155EEF] rb:text-lg" />,
      iconBg: 'rb:bg-[rgba(21,94,239,0.1)]',
      label: t('appShare.useShare'),
      desc: t('appShare.useShareDesc'),
    },
    {
      value: 'editable',
      icon: <CopyOutlined className="rb:text-[#369F21] rb:text-lg" />,
      iconBg: 'rb:bg-[rgba(54,159,33,0.1)]',
      label: t('appShare.copyShare'),
      desc: t('appShare.copyShareDesc'),
    },
  ];

  const handleOpen = async () => {
    setVisible(true);
    setSelectedSpaceIds([]);
    setPermission('readonly');
    setSearchKeyword('');
    try {
      const response = await getSpaceList();
      setSpaceList((response.items || []) as SpaceItem[]);
    } catch {
      setSpaceList([]);
    }
  };

  const handleClose = () => {
    setVisible(false);
    setLoading(false);
  };

  const handleShare = async () => {
    if (selectedSpaceIds.length === 0) {
      message.warning(t('appShare.selectWorkspace'));
      return;
    }
    setLoading(true);
    try {
      await shareAppToWorkspace(appId, { target_workspace_ids: selectedSpaceIds, permission });
      message.success(t('appShare.shareSuccess'));
      onSuccess?.();
      handleClose();
    } catch {
      message.error(t('appShare.shareFailed'));
    } finally {
      setLoading(false);
    }
  };

  useImperativeHandle(ref, () => ({ handleOpen, handleClose }));

  const filteredSpaceList = useMemo(
    () => spaceList.filter(item => item.name.toLowerCase().includes(searchKeyword.toLowerCase())),
    [spaceList, searchKeyword]
  );

  return (
    <RbModal
      title={t('appShare.title')}
      open={visible}
      onCancel={handleClose}
      onOk={handleShare}
      okText={t('appShare.confirm')}
      confirmLoading={loading}
      width={520}
    >
      <div className="rb:flex rb:flex-col rb:gap-4">
        {/* 目标工作空间选择 */}
        <div>
          <div className="rb:text-sm rb:font-medium rb:text-gray-800 rb:mb-2">
            {t('appShare.selectTarget')}
            {selectedSpaceIds.length > 0 && (
              <span className="rb:ml-2 rb:text-xs rb:text-[#155EEF] rb:font-normal">
                {t('appShare.selectedCount', { count: selectedSpaceIds.length })}
              </span>
            )}
          </div>
          <Input
            prefix={<SearchOutlined className="rb:text-gray-400" />}
            placeholder={t('appShare.searchWorkspace')}
            value={searchKeyword}
            onChange={e => setSearchKeyword(e.target.value)}
            className="rb:mb-2"
            allowClear
          />
          <div className="rb:flex rb:flex-col rb:gap-2 rb:max-h-[220px] rb:overflow-y-auto">
            {filteredSpaceList.length === 0 && (
              <div className="rb:text-center rb:text-gray-400 rb:py-6">{t('appShare.noWorkspace')}</div>
            )}
            {filteredSpaceList.map((item) => {
              const isSelected = selectedSpaceIds.includes(item.id)
              return (
                <div
                  key={item.id}
                  onClick={() => setSelectedSpaceIds(prev =>
                    isSelected ? prev.filter(id => id !== item.id) : [...prev, item.id]
                  )}
                  className={`rb:flex rb:items-center rb:justify-between rb:rounded-lg rb:p-3 rb:border rb:cursor-pointer rb:transition-all ${
                    isSelected
                      ? 'rb:border-[#155EEF] rb:bg-[rgba(21,94,239,0.06)]'
                      : 'rb:border-gray-200 rb:hover:border-[#155EEF] rb:hover:bg-[rgba(21,94,239,0.03)]'
                  }`}
                >
                  <span className="rb:text-sm rb:font-medium rb:text-gray-800">{item.name}</span>
                  {isSelected && <span className="rb:text-[#155EEF] rb:text-base">✓</span>}
                </div>
              )
            })}
          </div>
        </div>

        {/* 权限模式选择 */}
        <div>
          <div className="rb:text-sm rb:font-medium rb:text-gray-800 rb:mb-2">{t('appShare.permissionMode')}</div>
          <div className="rb:flex rb:flex-col rb:gap-2">
            {permissionOptions.map((opt) => (
              <div
                key={opt.value}
                onClick={() => setPermission(opt.value)}
                className={`rb:flex rb:items-center rb:gap-3 rb:p-4 rb:rounded-lg rb:border rb:cursor-pointer rb:transition-all ${
                  permission === opt.value
                    ? 'rb:border-[#155EEF] rb:bg-[rgba(21,94,239,0.06)]'
                    : 'rb:border-gray-200 rb:hover:border-[#155EEF] rb:hover:bg-[rgba(21,94,239,0.03)]'
                }`}
              >
                <div className={`rb:w-9 rb:h-9 rb:rounded-lg rb:flex rb:items-center rb:justify-center rb:flex-shrink-0 ${opt.iconBg}`}>
                  {opt.icon}
                </div>
                <div>
                  <div className="rb:text-sm rb:font-medium rb:text-gray-800">{opt.label}</div>
                  <div className="rb:text-xs rb:text-gray-500 rb:mt-0.5">{opt.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </RbModal>
  );
});

export default AppShareModal;
