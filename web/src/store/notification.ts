import { create } from 'zustand';
import {
  getNotifications,
  notificationBannerClose,
  notificationModalConfirm,
  notificationRead,
  notificationReadAll,
} from '@/api/notification';
import {
  configureNotificationRealtime,
  setupNotificationRealtime,
  teardownNotificationRealtime,
  updateNotificationRealtimeState,
} from './notification/realtime';
import type {
  BannerMessage,
  ModalMessage,
  NotificationState,
  NotificationSyncPayload,
} from './notification/types';
import {
  cleanSnoozed,
  emptyPagination,
  emptyStats,
  extractPaginatedMessages,
  isNotificationGeneration,
  isNotificationSyncPayload,
  loadSnoozedModals,
  NOTIFICATION_PAGE_SIZE,
  persistSnoozedModals,
} from './notification/utils';

export type * from './notification/types';
export { getNotificationTab, NOTIFICATION_PAGE_SIZE } from './notification/utils';

export const useNotification = create<NotificationState>((set, get) => {
  configureNotificationRealtime({
    applySync: (payload) => get().applyNotificationSync(payload),
    getSnapshot: () => {
      const state = get();
      return {
        cursor: state.cursor ?? '',
        generation: state.generation,
        changed: false,
        unread: state.notificationStats,
        banners: state.bannerMessages,
        modals: state.modalMessages,
      };
    },
  });

  return {
    messages: [],
    notificationStats: emptyStats(),
    loading: false,
    error: null,
    bannerMessages: [],
    modalMessages: [],
    snoozedModals: loadSnoozedModals(),
    pagination: emptyPagination(),
    lastFilter: null,
    cursor: null,
    generation: null,

    fetchMessages: async (filter) => {
      const page = 1;
      const pageSize = get().pagination.pageSize || NOTIFICATION_PAGE_SIZE;
      set({
        loading: true,
        error: null,
        lastFilter: filter ?? null,
        pagination: {
          ...get().pagination,
          page,
          pageSize,
          hasMore: true,
          loadingMore: false,
        },
      });

      try {
        const response = await getNotifications({
          ...filter,
          page,
          pagesize: pageSize,
        });
        const { messages, hasMore, ...rest } = extractPaginatedMessages(response, pageSize);
        set((state) => ({
          messages,
          pagination: {
            ...state.pagination,
            ...rest,
            page,
            pageSize,
            hasMore,
            loadingMore: false,
          },
        }));
      } catch (error) {
        set({
          error: error instanceof Error ? error.message : 'Failed to load notifications',
          pagination: { ...get().pagination, loadingMore: false },
        });
      } finally {
        set({ loading: false });
      }
    },

    loadMore: async () => {
      const { lastFilter, pagination, loading } = get();
      if (loading || pagination.loadingMore || !pagination.hasMore || !lastFilter) return;

      const nextPage = pagination.page + 1;
      const pageSize = pagination.pageSize || NOTIFICATION_PAGE_SIZE;
      set({ pagination: { ...pagination, loadingMore: true } });

      try {
        const response = await getNotifications({
          ...lastFilter,
          page: nextPage,
          pagesize: pageSize,
        });
        const pageResult = extractPaginatedMessages(response, pageSize);

        set((state) => {
          const existingIds = new Set(state.messages.map((message) => message.id));
          const uniqueMessages = pageResult.messages.filter(
            (message) => !existingIds.has(message.id),
          );
          return {
            messages: [...state.messages, ...uniqueMessages],
            pagination: {
              ...state.pagination,
              page: nextPage,
              pageSize,
              hasMore: pageResult.hasMore,
              loadingMore: false,
            },
          };
        });
      } catch (error) {
        set((state) => ({
          error: error instanceof Error ? error.message : 'Failed to load notifications',
          pagination: { ...state.pagination, loadingMore: false },
        }));
      }
    },

    markAsRead: async (id) => {
      try {
        await notificationRead(id);
      } catch {
        // Realtime synchronization will eventually restore server state.
      }
    },

    markAllAsRead: async () => {
      try {
        await notificationReadAll();
      } catch {
        // Realtime synchronization will eventually restore server state.
      }
    },

    closeBanner: async (id) => {
      try {
        await notificationBannerClose(id);
      } catch {
        // Realtime synchronization will eventually restore server state.
      }
    },

    confirmMessage: async (id) => {
      try {
        await notificationModalConfirm(id);
      } catch {
        // Realtime synchronization will eventually restore server state.
      }
    },

    snoozeModalMessage: (id, hours = 1) => {
      const safeHours = Number.isFinite(hours) ? Math.min(Math.max(0, hours), 24 * 365) : 1;
      const snoozeUntil = Date.now() + safeHours * 60 * 60 * 1_000;
      const next = cleanSnoozed({ ...get().snoozedModals, [id]: snoozeUntil });
      persistSnoozedModals(next);
      set((state) => ({
        snoozedModals: next,
        modalMessages: state.modalMessages.filter((message) => message.id !== id),
      }));
    },

    applyNotificationSync: (payload: NotificationSyncPayload) => {
      if (!isNotificationSyncPayload(payload)) return;
      const state = get();

      const cursor = payload.cursor || state.cursor;
      const generation = isNotificationGeneration(payload.generation)
        ? payload.generation
        : state.generation;
      updateNotificationRealtimeState(cursor, generation);

      const stats = payload.unread
        ? {
            total: payload.unread.total,
            system: payload.unread.system,
            announcement: payload.unread.announcement,
          }
        : undefined;

      let banners: BannerMessage[] | undefined;
      if (Array.isArray(payload.banners)) banners = payload.banners;
      else if (payload.banners === null) banners = [];

      let modals: ModalMessage[] | undefined;
      let snoozedModals: Record<string, number> | undefined;
      if (Array.isArray(payload.modals) || payload.modals === null) {
        const cleanedSnoozed = cleanSnoozed(state.snoozedModals);
        const rawModals = Array.isArray(payload.modals) ? payload.modals : [];
        modals = rawModals.filter((message) => !cleanedSnoozed[message.id]);
        snoozedModals = cleanedSnoozed;
        if (Object.keys(cleanedSnoozed).length !== Object.keys(state.snoozedModals).length) {
          persistSnoozedModals(cleanedSnoozed);
        }
      }

      set({
        cursor,
        generation,
        ...(stats ? { notificationStats: stats } : {}),
        ...(banners !== undefined ? { bannerMessages: banners } : {}),
        ...(modals !== undefined ? { modalMessages: modals } : {}),
        ...(snoozedModals ? { snoozedModals } : {}),
      });
    },

    setupRealtime: setupNotificationRealtime,
    teardownRealtime: teardownNotificationRealtime,
  };
});
