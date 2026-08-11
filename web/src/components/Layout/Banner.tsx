import { useEffect, useRef, type FC } from 'react';
import { Alert, App } from 'antd';
import Marquee from 'react-fast-marquee';
import { useTranslation } from 'react-i18next';

import {
  useNotification,
} from '@/store/notification';
import { isPrivateAvailable } from '@/utils/private'
import RbMarkdown from '@/components/Markdown';

const Banners: FC<{ className?: string }> = ({
  className
}) => {
  if (!isPrivateAvailable) return;

  const { t } = useTranslation();
  const { modal } = App.useApp()
  const {
    bannerMessages,
    modalMessages,
    confirmMessage,
    closeBanner,
    snoozeModalMessage,
    markAsRead,
    setupRealtime,
    teardownRealtime,
  } = useNotification();

  // Single-instance lock + active modal identity/destroy handle for cleanup
  const openModalRef = useRef(false);
  const currentModalRef = useRef<{ id: string; token: symbol } | null>(null);
  const destroyModalRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    setupRealtime();
  }, [setupRealtime]);

  useEffect(() => {
    if (openModalRef.current) {
      const currentModal = currentModalRef.current;
      const currentMessageStillExists = currentModal
        ? modalMessages.some((message) => message.id === currentModal.id)
        : false;
      if (currentMessageStillExists) return;

      const destroyCurrentModal = destroyModalRef.current;
      openModalRef.current = false;
      currentModalRef.current = null;
      destroyModalRef.current = null;
      if (destroyCurrentModal) {
        try { destroyCurrentModal(); } catch { /* noop */ }
      }
    }

    if (modalMessages.length === 0) return;
    const next = modalMessages[0];
    const modalToken = Symbol(next.id);

    const isConfirmRequired = Boolean(next.requires_confirmation);
    openModalRef.current = true;
    currentModalRef.current = { id: next.id, token: modalToken };

    const releaseLock = () => {
      if (currentModalRef.current?.token !== modalToken) return;
      openModalRef.current = false;
      currentModalRef.current = null;
      destroyModalRef.current = null;
    };
    let destroy = null;

    const okText = t('notificationCenter.actions.confirm');
    const cancelText = t('notificationCenter.actions.remindLater');

    if (isConfirmRequired) {
      destroy = modal.confirm({
        title: next.title,
        content: <RbMarkdown content={next.content} />,
        maskClosable: false,
        closable: false,
        keyboard: false,
        centered: true,
        okText,
        cancelText,
        onOk: async () => {
          try {
            await confirmMessage(next.id);
          } finally {
            releaseLock();
          }
        },
        onCancel: () => {
          // Handles both the 稍后 button AND the X-close of closable=true modals.
          // requires_confirmation=false 时 closable=false + okCancel=false，因此 onCancel 不会被触发
          snoozeModalMessage(next.id, 1);
          releaseLock();
        },
      });
    } else {
      destroy = modal.warning({
        title: next.title,
        content: next.content,
        icon: false,
        closable: true,
        okText,
        onOk: async () => {
          try {
            await markAsRead(next.id);
          } finally {
            releaseLock();
          }
        },
        onCancel: releaseLock,
      });
    }

    destroyModalRef.current = (() => { destroy.destroy(); }) as () => void;
  }, [modalMessages, modal, confirmMessage, snoozeModalMessage, markAsRead, t]);

  // When the Banners host unmounts (e.g. route change), ensure the dangling
  // imperative modal is torn down and the global lock is reset.
  useEffect(() => {
    return () => {
      if (destroyModalRef.current) {
        try { destroyModalRef.current(); } catch { /* noop */ }
      }
      openModalRef.current = false;
      currentModalRef.current = null;
      destroyModalRef.current = null;
      teardownRealtime();
    };
  }, [teardownRealtime]);

  const firstBanner = bannerMessages[0];
  if (!firstBanner) {
    return null;
  }

  return (
    <Alert
      key={firstBanner.id}
      type={firstBanner.theme === 'orange' ? 'warning' : 'error'}
      banner
      message={
        <Marquee pauseOnHover gradient={false}>
            {firstBanner.title} {firstBanner.summary}
        </Marquee>
      }
      closable
      onClose={() => {
        void closeBanner(firstBanner.id);
      }}
      className={className || `rb:mb-3!`}
    />
  )
};
export default Banners;
