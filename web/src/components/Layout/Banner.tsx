import { useEffect, useRef, useState, type FC } from 'react';
import { Alert } from 'antd';
import Marquee from 'react-fast-marquee';
import { useTranslation } from 'react-i18next';

import {
  useNotification,
} from '@/store/notification';
import { isPrivateAvailable } from '@/utils/private'
import RbMarkdown from '@/components/Markdown';
import RbModal from '@/components/RbModal';
import type { ModalMessage } from '@/store/notification/types';

type ModalMode = 'confirm' | 'warning';

interface ActiveModalState {
  mode: ModalMode;
  message: ModalMessage;
  token: symbol;
}

const Banners: FC<{ className?: string }> = ({
  className
}) => {
  if (!isPrivateAvailable) return;

  const { t } = useTranslation();
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

  // Single-instance lock + active modal identity for cleanup / stale-close guard.
  const openModalRef = useRef(false);
  const currentModalRef = useRef<{ id: string; token: symbol } | null>(null);

  /** Controlled <RbModal> state (replaces imperative modal.confirm / .warning). */
  const [activeModal, setActiveModal] = useState<ActiveModalState | null>(null);

  useEffect(() => {
    setupRealtime();
  }, [setupRealtime]);

  /**
   * Close the currently displayed modal, but only if the caller still owns the
   * token (i.e. a subsequent message hasn't already replaced it).
   */
  const closeActiveModal = (token: symbol) => {
    if (currentModalRef.current?.token !== token) return;
    openModalRef.current = false;
    currentModalRef.current = null;
    setActiveModal((current) => (current?.token === token ? null : current));
  };

  useEffect(() => {
    if (openModalRef.current) {
      const currentModal = currentModalRef.current;
      const currentMessageStillExists = currentModal
        ? modalMessages.some((message) => message.id === currentModal.id)
        : false;
      if (currentMessageStillExists) return;

      const token = currentModal?.token;
      openModalRef.current = false;
      currentModalRef.current = null;
      if (token) {
        setActiveModal((current) => (current?.token === token ? null : current));
      }
    }

    if (modalMessages.length === 0) return;
    const next = modalMessages[0];
    const modalToken = Symbol(next.id);

    const isConfirmRequired = Boolean(next.requires_confirmation);
    openModalRef.current = true;
    currentModalRef.current = { id: next.id, token: modalToken };

    setActiveModal({
      mode: isConfirmRequired ? 'confirm' : 'warning',
      message: next,
      token: modalToken,
    });
  }, [modalMessages, confirmMessage, snoozeModalMessage, markAsRead]);

  // When the Banners host unmounts (e.g. route change), ensure the dangling
  // imperative modal is torn down and the global lock is reset.
  useEffect(() => {
    return () => {
      openModalRef.current = false;
      currentModalRef.current = null;
      teardownRealtime();
    };
  }, [teardownRealtime]);

  /* ----------------------------- Render helpers ----------------------------- */

  const handleConfirmOk = async () => {
    if (!activeModal || activeModal.mode !== 'confirm') return;
    try {
      await confirmMessage(activeModal.message.id);
    } finally {
      closeActiveModal(activeModal.token);
    }
  };

  const handleConfirmCancel = () => {
    if (!activeModal || activeModal.mode !== 'confirm') return;
    // Handles both the 稍后 button AND closable=true X-close; requires_confirmation
    // uses okCancel=true so onCancel is only fired by those two interactions.
    snoozeModalMessage(activeModal.message.id, 1);
    closeActiveModal(activeModal.token);
  };

  const handleWarningOk = async () => {
    if (!activeModal || activeModal.mode !== 'warning') return;
    try {
      await markAsRead(activeModal.message.id);
    } finally {
      closeActiveModal(activeModal.token);
    }
  };

  const handleWarningCancel = () => {
    if (!activeModal || activeModal.mode !== 'warning') return;
    closeActiveModal(activeModal.token);
  };

  const renderRbModal = () => {
    if (!activeModal) return null;
    const { mode, message } = activeModal;
    console.log(activeModal);
    const confirmOkText = t('notificationCenter.actions.confirm');
    const remindLaterText = t('notificationCenter.actions.remindLater');

    if (mode === 'confirm') {
      return (
        <RbModal
          open
          title={message.title}
          maskClosable={false}
          closable={false}
          keyboard={false}
          centered
          okText={confirmOkText}
          cancelText={remindLaterText}
          onOk={handleConfirmOk}
          onCancel={handleConfirmCancel}
        >
          <RbMarkdown content={message.content} />
        </RbModal>
      );
    }

    return (
      <RbModal
        open
        title={message.title}
        closable
        okText={confirmOkText}
        onOk={handleWarningOk}
        onCancel={handleWarningCancel}
        cancelButtonProps={{ style: { display: 'none' } }}
        className="rb-banner-warning-modal"
      >
        <RbMarkdown content={message.content} />
      </RbModal>
    );
  };

  /* ------------------------------- Top Alert -------------------------------- */

  const firstBanner = bannerMessages[0];

  return (
    <>
      {firstBanner && (
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
      )}
      {renderRbModal()}
    </>
  );
};

export default Banners;
