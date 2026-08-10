import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { Badge, Button, Checkbox, Modal, Popover, Spin, Tag } from 'antd';
import ReactMarkdown from 'react-markdown';
import { useTranslation } from 'react-i18next';
// import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';

import {
  useNotification,
  type NotificationMessage,
  type NotificationMessageTab,
} from '@/store/notification';
import styles from './index.module.css';
import { formatDateTime } from '@/utils/format';
import Empty from '@/components/Empty';

const BellIcon = () => (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

interface NotificationPanelProps {
  open: boolean;
}

const NotificationPanel = ({ open }: NotificationPanelProps) => {
  const { t } = useTranslation();
  // const navigate = useNavigate();
  const {
    messages,
    loading,
    notificationStats,
    pagination,
    markAsRead,
    markAllAsRead,
    confirmMessage,
    fetchMessages,
    loadMore,
    cursor,
    generation
  } = useNotification();
  const [tab, setTab] = useState<NotificationMessageTab>('system');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [selected, setSelected] = useState<NotificationMessage | null>(null);

  const listRef = useRef<HTMLDivElement | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // Refs hold latest values so IntersectionObserver (created once) avoids stale closures
  // See experience 1255549 for the "observer callback sees old page/hasMore" class of bug.
  const loadingRef = useRef(loading);
  loadingRef.current = loading;
  const loadingMoreRef = useRef(pagination.loadingMore);
  loadingMoreRef.current = pagination.loadingMore;
  const hasMoreRef = useRef(pagination.hasMore);
  hasMoreRef.current = pagination.hasMore;
  const loadMoreRef = useRef(loadMore);
  loadMoreRef.current = loadMore;

  useEffect(() => {
    if (!open) return;

    const filter: { tab: NotificationMessageTab; is_read?: boolean } = { tab };
    if (unreadOnly) filter.is_read = false;
    void fetchMessages(filter);
  }, [open, tab, unreadOnly, fetchMessages, cursor, generation]);

  // Single observer instance, bound to the sentinel, with cleanup on unmount.
  useEffect(() => {
    const root = listRef.current;
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (
            entry.isIntersecting &&
            hasMoreRef.current &&
            !loadingMoreRef.current &&
            !loadingRef.current
          ) {
            void loadMoreRef.current();
          }
        });
      },
      { root: root || null, rootMargin: '80px', threshold: 0 },
    );
    observer.observe(sentinel);
    return () => {
      observer.unobserve(sentinel);
      observer.disconnect();
    };
  }, []);

  const openDetail = (message: NotificationMessage) => {
    if (!message.is_read) {
      void markAsRead(message.id);
    }
    // setSelected(message);
  };

  const handleConfirm = (event: MouseEvent, id: string) => {
    event.stopPropagation();
    void confirmMessage(id);
  };

  const confirmSelected = () => {
    if (!selected) return;
    void confirmMessage(selected.id);
  };

  const handleMarkAllRead = () => {
    void markAllAsRead();
  };

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <span className={styles.panelTitle}>{t('notificationCenter.title')}</span>
        <Button
          type="link"
          size="small"
          onClick={handleMarkAllRead}
          disabled={notificationStats.total === 0}
        >
          {t('notificationCenter.actions.markAllRead')}
        </Button>
      </div>
      <div className={styles.tabs} role="tablist">
        {(['system', 'announcement'] as NotificationMessageTab[]).map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={tab === item}
            className={clsx(styles.tab, tab === item && styles.tabActive)}
            onClick={() => setTab(item)}
          >
            {t(`notificationCenter.tabs.${item}`)}
            <span className={styles.tabCount}>{notificationStats[item]}</span>
          </button>
        ))}
      </div>
      <div className={styles.toolbar}>
        <Checkbox checked={unreadOnly} onChange={(event) => setUnreadOnly(event.target.checked)}>
          {t('notificationCenter.actions.unreadOnly')}
        </Checkbox>
        <span className="rb:text-[11px] rb:text-[#A8A9AA]">{messages.length}</span>
      </div>
      <div className={styles.list} ref={listRef}>
        {messages.length === 0 ? (
          <Empty
            size={88}
            subTitle={t(unreadOnly ? 'notificationCenter.empty.unread' : 'notificationCenter.empty.all')}
            className="rb:py-10!"
          />
        ) : (
          <>
            {messages.map((message) => {
              return (
                <div
                  key={message.id}
                  className={clsx(styles.item, !message.is_read && styles.itemUnread)}
                  role="button"
                  tabIndex={0}
                  onClick={() => openDetail(message)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') openDetail(message);
                  }}
                >
                  <div className={styles.itemContent}>
                    <div className={styles.itemTitleLine}>
                      {!message.is_read && <span className={styles.unreadDot} />}
                      {message.priority !== 'normal' && (
                        <Tag
                          color={message.priority === 'pinned' ? 'red' : 'orange'}
                          className="rb:text-[10px]! rb:leading-4! rb:m-0!"
                        >
                          {t(`notificationCenter.priorities.${message.priority}`)}
                        </Tag>
                      )}
                      {message.type !== 'announcement' &&
                        <Tag
                          color={message.type === 'activity' ? 'orange' : 'default'}
                          className="rb:text-[10px]! rb:leading-4! rb:m-0!"
                        >
                          {t(`notificationCenter.types.${message.type}`)}
                        </Tag>
                      }
                      <span className={styles.itemTitle}>{message.title}</span>
                    </div>
                    <div className={styles.itemSummary}>{message.summary}</div>
                    <div className={styles.itemMeta}>
                      <span>{formatDateTime(message.published_at)}</span>
                      {message.requires_confirmation &&
                        (message.is_confirmed ? (
                          <span className="rb:text-[#12B76A]">
                            {t('notificationCenter.actions.confirmed')}
                          </span>
                        ) : (
                          <Button danger size="small" onClick={(event) => handleConfirm(event, message.id)}>
                            {t('notificationCenter.actions.confirm')}
                          </Button>
                        ))}
                    </div>
                  </div>
                </div>
              );
            })}

            {/* Trigger for infinite scroll */}
            <div ref={sentinelRef} className="rb:max-h-[calc(100vh-200px)]" aria-hidden />
            {pagination.loadingMore && (
              <div className="rb:py-5 rb:flex rb:justify-center">
                <Spin size="small" />
              </div>
            )}
            {!pagination.loadingMore && !pagination.hasMore && (
              <div className="rb:py-2 rb:text-center rb:text-[11px] rb:text-[#B8BAC0]">
                — {t('notificationCenter.empty.noMore', { defaultValue: '没有更多了' })} —
              </div>
            )}
          </>
        )}
      </div>

      <Modal
        open={Boolean(selected)}
        title={t('notificationCenter.detail.title')}
        footer={
          <Button type="primary" onClick={() => setSelected(null)}>
            {t('notificationCenter.actions.close')}
          </Button>
        }
        onCancel={() => setSelected(null)}
        width={560}
      >
        {selected && (
          <>
            <h3 className="rb:text-[17px] rb:font-semibold rb:mt-2 rb:mb-0">{selected.title}</h3>
            <div className={styles.detailMeta}>
              <span>
                {t('notificationCenter.detail.publishedAt')}: {formatDateTime(selected.published_at)}
              </span>
            </div>
            <div className={styles.markdown}>
              <ReactMarkdown>{selected.content}</ReactMarkdown>
            </div>
            {selected.requires_confirmation && !selected.is_confirmed && (
              <Button danger className="rb:mt-4!" onClick={confirmSelected}>
                {t('notificationCenter.actions.confirm')}
              </Button>
            )}
          </>
        )}
      </Modal>
    </div>
  );
};

const NotificationBell = () => {
  const { t } = useTranslation();
  const { notificationStats } = useNotification();
  const [open, setOpen] = useState(false);

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
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
