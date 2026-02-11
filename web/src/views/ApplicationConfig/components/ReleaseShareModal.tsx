/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:28:46 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:28:46 
 */
/**
 * Release Share Modal
 * Generates and displays a shareable link for a specific application version
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Button, App } from 'antd';
import { ExclamationCircleFilled } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import copy from 'copy-to-clipboard'

import type { Release, ReleaseShareModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { shareRelease } from '@/api/application'
import RbAlert from '@/components/RbAlert'

/**
 * Component props
 */
interface ReleaseShareModalProps {
  /** Version to share */
  version: Release | null
}

/**
 * Modal for sharing application versions
 */
const ReleaseShareModal = forwardRef<ReleaseShareModalRef, ReleaseShareModalProps>(({
  version
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [shareLink, setShareLink] = useState<string | null>(null)

  /** Close modal */
  const handleClose = () => {
    setVisible(false);
    setLoading(false)
  };

  /** Open modal and generate share link */
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
  /** Copy share link to clipboard */
  const handleCopy = () => {
    if (!shareLink) return
    copy(shareLink)
    message.success(t('common.copySuccess'))
  }

  /** Expose methods to parent component */
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
        <div className="rb:leading-5 rb:mb-2">{t('application.shareLink')}</div>
        <div className="rb:mb-3 rb:flex rb:items-center rb:gap-2.5 rb:justify-between">
          <div className="rb:overflow-hidden rb:whitespace-nowrap rb:text-ellipsis rb:cursor-pointer rb:h-8 rb:p-[6px_10px] rb:bg-[#FFFFFF] rb:border rb:border-[#EBEBEB] rb:rounded-md rb:leading-5">{shareLink}</div>

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