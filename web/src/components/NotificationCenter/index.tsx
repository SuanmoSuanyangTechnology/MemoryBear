import { useEffect, useState } from 'react';
import { Badge, Button, Popover } from 'antd';
import { useTranslation } from 'react-i18next';

import {
  useNotification,
} from '@/store/notification';
import styles from './index.module.css';
import NotificationPanel from './NotificationPanel';

const BellIcon = () => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const NotificationBell = () => {
  const { t } = useTranslation();

  const { notificationStats, fetchMessages } = useNotification();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return
    fetchMessages({ tab: 'system' })
  }, [open])

  return (
    <Popover
      open={open}
      onOpenChange={(newOpen) => setOpen(newOpen)}
      placement="bottomRight"
      trigger="click"
      arrow={false}
      content={<NotificationPanel open={open} />}
      styles={{
        body: {
          padding: 0,
          borderRadius: 14,
          overflow: 'hidden',
          boxShadow: '0 12px 36px rgba(16, 24, 40, 0.16)',
        },
      }}
    >
      <Badge
        count={notificationStats.total}
        size="small"
        overflowCount={99}
        offset={[-1, 1]}
      >
        <Button
          className={styles.bellButton}
          icon={<BellIcon />}
          aria-label={t('notificationCenter.bellAria', { count: notificationStats.total })}
        />
      </Badge>
    </Popover>
  );
};

export default NotificationBell;
