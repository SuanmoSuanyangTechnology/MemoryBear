import type {
  NotificationGeneration,
  NotificationMessage,
  NotificationMessageTab,
  NotificationStats,
  NotificationSyncPayload,
  NotificationType,
  PaginationState,
} from './types';

interface PageMeta {
  page?: number;
  pagesize?: number;
  page_size?: number;
  total?: number;
  hasnext?: boolean;
  has_next?: boolean;
  has_more?: boolean;
}

interface NotificationListResponse {
  items?: NotificationMessage[];
  list?: NotificationMessage[];
  records?: NotificationMessage[];
  data?: NotificationMessage[] | NotificationListResponse;
  page?: PageMeta;
  total?: number;
  has_more?: boolean;
  has_next?: boolean;
}

interface PaginatedMessages {
  messages: NotificationMessage[];
  hasMore: boolean;
  page: number;
  pageSize: number;
  total: number;
}

export const NOTIFICATION_PAGE_SIZE = 10;

const MODAL_SNOOZE_STORAGE_KEY = 'memorybear.notification.modalSnooze';
const MAX_SNOOZED_MODALS = 500;
const MAX_IDENTIFIER_LENGTH = 256;
const MAX_SYNC_COLLECTION_SIZE = 500;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

const isSafeIdentifier = (value: unknown): value is string =>
  typeof value === 'string'
  && value.length > 0
  && value.length <= MAX_IDENTIFIER_LENGTH;

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

const isSyncCollection = (value: unknown, contentKey: 'summary' | 'content'): boolean => {
  if (value === null || value === undefined) return true;
  if (!Array.isArray(value) || value.length > MAX_SYNC_COLLECTION_SIZE) return false;

  return value.every((item) => (
    isRecord(item)
    && isSafeIdentifier(item.id)
    && typeof item.title === 'string'
    && typeof item[contentKey] === 'string'
  ));
};

export const getNotificationTab = (type: NotificationType): NotificationMessageTab =>
  type === 'announcement' || type === 'activity' ? 'announcement' : 'system';

export const emptyStats = (): NotificationStats => ({
  total: 0,
  system: 0,
  announcement: 0,
});

export const emptyPagination = (
  pageSize: number = NOTIFICATION_PAGE_SIZE,
): PaginationState => ({
  page: 1,
  pagesize: pageSize,
  pageSize,
  hasMore: true,
  loadingMore: false,
  total: 0,
  has_more: false,
});

export const extractPaginatedMessages = (
  response: unknown,
  requestedPageSize: number = NOTIFICATION_PAGE_SIZE,
): PaginatedMessages => {
  const empty: PaginatedMessages = {
    messages: [],
    hasMore: false,
    page: 1,
    pageSize: requestedPageSize,
    total: 0,
  };

  if (Array.isArray(response)) {
    const messages = response as NotificationMessage[];
    return {
      ...empty,
      messages,
      hasMore: messages.length >= requestedPageSize,
      total: messages.length,
    };
  }

  if (!isRecord(response)) return empty;
  const root = response as NotificationListResponse & Record<string, unknown>;
  let payload: NotificationListResponse = root;
  if (isRecord(root.data)) {
    payload = root.data as NotificationListResponse;
  } else if (Array.isArray(root.data)) {
    const messages = root.data as NotificationMessage[];
    return {
      ...empty,
      messages,
      ...root.page,
      hasMore: root.page?.hasnext ?? false,
      total: messages.length,
    };
  }

  const messages = (
    Array.isArray(payload.items) ? payload.items
    : Array.isArray(payload.list) ? payload.list
    : Array.isArray(payload.records) ? payload.records
    : []
  ) as NotificationMessage[];

  const pageMeta = isRecord(payload.page) ? payload.page as PageMeta : undefined;
  const page = isFiniteNumber(pageMeta?.page) ? pageMeta.page : 1;
  const pageSize = isFiniteNumber(pageMeta?.pagesize)
    ? pageMeta.pagesize
    : isFiniteNumber(pageMeta?.page_size)
      ? pageMeta.page_size
      : requestedPageSize;
  const total = isFiniteNumber(payload.total)
    ? payload.total
    : isFiniteNumber(pageMeta?.total)
      ? pageMeta.total
      : messages.length;

  const hasMore = pageMeta
    ? typeof pageMeta.hasnext === 'boolean'
      ? pageMeta.hasnext
      : typeof pageMeta.has_next === 'boolean'
        ? pageMeta.has_next
        : typeof pageMeta.has_more === 'boolean'
          ? pageMeta.has_more
          : messages.length >= pageSize
    : typeof payload.has_more === 'boolean'
      ? payload.has_more
      : typeof payload.has_next === 'boolean'
        ? payload.has_next
        : messages.length >= pageSize;

  return { messages, hasMore, page, pageSize, total };
};

export const isNotificationGeneration = (
  value: unknown,
): value is NotificationGeneration => (
  (typeof value === 'string' && value.length > 0 && value.length <= MAX_IDENTIFIER_LENGTH)
  || isFiniteNumber(value)
);

export const isNotificationSyncPayload = (
  value: unknown,
): value is NotificationSyncPayload => {
  if (!isRecord(value)) return false;
  if (typeof value.cursor !== 'string' || value.cursor.length > 4_096) return false;
  if (typeof value.changed !== 'boolean') return false;
  if (
    value.generation !== undefined
    && value.generation !== null
    && !isNotificationGeneration(value.generation)
  ) return false;

  if (value.unread !== undefined && value.unread !== null) {
    if (!isRecord(value.unread)) return false;
    const stats = value.unread;
    if (
      !isFiniteNumber(stats.total)
      || !isFiniteNumber(stats.system)
      || !isFiniteNumber(stats.announcement)
    ) return false;
  }

  return isSyncCollection(value.banners, 'summary')
    && isSyncCollection(value.modals, 'content');
};

export const cleanSnoozed = (
  snoozed: Record<string, unknown>,
): Record<string, number> => {
  const now = Date.now();
  const cleaned = Object.create(null) as Record<string, number>;

  for (const [id, timestamp] of Object.entries(snoozed).slice(0, MAX_SNOOZED_MODALS)) {
    if (isSafeIdentifier(id) && isFiniteNumber(timestamp) && timestamp > now) {
      cleaned[id] = timestamp;
    }
  }
  return cleaned;
};

export const loadSnoozedModals = (): Record<string, number> => {
  try {
    if (typeof localStorage === 'undefined') return {};
    const raw = localStorage.getItem(MODAL_SNOOZE_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return isRecord(parsed) ? cleanSnoozed(parsed) : {};
  } catch {
    return {};
  }
};

export const persistSnoozedModals = (snoozed: Record<string, number>) => {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(
      MODAL_SNOOZE_STORAGE_KEY,
      JSON.stringify(cleanSnoozed(snoozed)),
    );
  } catch {
    // Storage can be unavailable in private mode or when quota is exhausted.
  }
};
