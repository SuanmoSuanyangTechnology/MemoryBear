export type NotificationMessageTab = 'system' | 'announcement';
export type NotificationType = 'system' | 'announcement' | 'activity';
export type NotificationPriority = 'normal' | 'important' | 'pinned';
export type NotificationStatus =
  | 'draft'
  | 'pending_approval'
  | 'publishing'
  | 'scheduled'
  | 'published'
  | 'expired'
  | 'withdrawn';
export type PublishMode = 'immediate' | 'scheduled';
export type OfflineMode = 'permanent' | 'time';
export type DisplayForm = 'in_app' | 'banner' | 'modal' | 'confirmation';
export type DeliveryChannel = 'in_app';

export interface NotificationMessage {
  id: string;
  title: string;
  content: string;
  summary: string;
  type: NotificationType;
  priority: NotificationPriority;
  display_forms: DisplayForm[];
  expire_at: number | null;
  published_at: number | null;
  version: number;
  is_read: boolean;
  is_confirmed: boolean;
  requires_confirmation: boolean;
  read_at: number;

  alert_severity: 'P0' | 'P1' | 'P2' | 'P3';
}

export interface BannerMessage {
  id: string;
  title: string;
  summary: string;
  type: NotificationType;
  priority: NotificationPriority;
  version: number;
  theme: 'orange';
  published_at: number;
  requires_confirmation: boolean;
}

export interface ModalMessage {
  id: string;
  title: string;
  content: string;
  type: NotificationType;
  priority: NotificationPriority;
  version: number;
  theme: 'orange';
  published_at: number;
  requires_confirmation: boolean;
}

export interface NotificationStats {
  total: number;
  system: number;
  announcement: number;
}

export type NotificationGeneration = string | number;

export interface NotificationSyncPayload {
  cursor: string;
  generation?: NotificationGeneration | null;
  changed: boolean;
  unread?: NotificationStats | null;
  banners?: BannerMessage[] | null;
  modals?: ModalMessage[] | null;
  refresh_list?: boolean | null;
  server_time?: number | null;
}

export type NotificationMessagesFilter = {
  is_read?: boolean;
  tab: NotificationMessageTab;
};

export interface PaginationState {
  page: number;
  pageSize: number;
  hasMore: boolean;
  loadingMore: boolean;
  has_more: boolean;
  pagesize: number;
  total: number;
}

export interface NotificationState {
  messages: NotificationMessage[];
  notificationStats: NotificationStats;
  loading: boolean;
  error: string | null;
  bannerMessages: BannerMessage[];
  modalMessages: ModalMessage[];
  snoozedModals: Record<string, number>;
  pagination: PaginationState;
  lastFilter: NotificationMessagesFilter | null;
  cursor: string | null;
  generation: NotificationGeneration | null;

  fetchMessages: (filter?: NotificationMessagesFilter) => Promise<void>;
  loadMore: () => Promise<void>;
  markAsRead: (id: string, tab?: NotificationMessageTab) => Promise<void>;
  markAllAsRead: (tab?: NotificationMessageTab) => Promise<void>;
  closeBanner: (id: string) => Promise<void>;
  confirmMessage: (id: string, tab?: NotificationMessageTab) => Promise<void>;
  snoozeModalMessage: (id: string, hours?: number) => void;

  applyNotificationSync: (payload: NotificationSyncPayload) => void;
  setupRealtime: () => void;
  teardownRealtime: () => void;
}
