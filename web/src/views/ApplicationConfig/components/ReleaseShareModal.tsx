import { forwardRef, useImperativeHandle, useState } from 'react';
import { Button, App } from 'antd';
import { ExclamationCircleFilled } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import copy from 'copy-to-clipboard'

import type { Release, ReleaseShareModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { shareRelease } from '@/api/application'
import RbAlert from '@/components/RbAlert'

interface ReleaseShareModalProps {
  version: Release | null
}

const ReleaseShareModal = forwardRef<ReleaseShareModalRef, ReleaseShareModalProps>(({
  version
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [shareLink, setShareLink] = useState<string | null>(null)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    setLoading(false)
  };

  const handleOpen = () => {
    if (!version) {
      return
    }
    setLoading(true)
    shareRelease(version?.app_id, version.id || '')
      .then(res => {
        const response = res as { share_token: string }
        if (response?.share_token) {
          setShareLink(`${window.location.origin}/#/conversation/${response?.share_token}`)
          setVisible(true);
        }
      })
      .finally(() => {
        setLoading(false)
      })
  };
  const handleCopy = () => {
    if (!shareLink) return
    copy(shareLink)
    message.success(t('common.copySuccess'))
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={<>{t('application.shareVersion')} {version?.version}</>}
      open={visible}
      onCancel={handleClose}
      footer={false}
    >
      <>
        <div className="rb:leading-[20px] rb:mb-[8px]">{t('application.shareLink')}</div>
        <div className="rb:mb-[12px] rb:flex rb:items-center rb:gap-[10px] rb:justify-between">
          <div className="rb:overflow-hidden rb:whitespace-nowrap rb:text-ellipsis rb:cursor-pointer rb:h-[32px] rb:p-[6px_10px] rb:bg-[#FFFFFF] rb:border rb:border-[#EBEBEB] rb:rounded-[6px] rb:leading-[20px]">{shareLink}</div>

          <Button type="primary" loading={loading} disabled={!shareLink} onClick={handleCopy}>
            {t('common.copy')}
          </Button>
        </div>
        <RbAlert color="orange" icon={<ExclamationCircleFilled />}>
          {t('application.shareLinkTip')}
        </RbAlert>
      </>
    </RbModal>
  );
});

export default ReleaseShareModal;