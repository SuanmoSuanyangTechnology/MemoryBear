import { notificationsEvents, notificationSync } from '@/api/notification';
import type { SSEMessage } from '@/utils/stream';
import type {
  NotificationGeneration,
  NotificationSyncPayload,
} from './types';
import {
  isNotificationGeneration,
  isNotificationSyncPayload,
} from './utils';

interface RealtimeBridge {
  applySync: (payload: NotificationSyncPayload) => void;
  getSnapshot: () => NotificationSyncPayload;
}

type NotificationBroadcastMessage =
  | {
      type: 'sync';
      payload: NotificationSyncPayload;
      targetTabId?: string;
    }
  | {
      type: 'state.request';
      senderTabId: string;
    };

const BROADCAST_CHANNEL_NAME = 'memorybear:notifications:sync';
const SSE_LEADER_LOCK_NAME = 'memorybear:notifications:sse-leader';
const SSE_RECONNECT_DELAY_MS = 30_000;
const SYNC_POLL_INTERVAL_MS = 30_000;
const MAX_CHANNEL_ID_LENGTH = 256;
const MAX_CURSOR_LENGTH = 4_096;
const TAB_ID = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
  ? crypto.randomUUID()
  : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

interface PendingSyncRequest {
  targetCursor: string | null;
  allowResponseCursor: boolean;
}

let bridge: RealtimeBridge | null = null;
let channel: BroadcastChannel | null = null;
let sseAbort: (() => void) | null = null;
let sseStarted = false;
let sseConnected = false;
let realtimeEnabled = false;
let isSSELeader = false;
let leaderElectionStarted = false;
let leaderElectionController: AbortController | null = null;
let releaseSSELeadership: (() => void) | null = null;
let sseConnectionId = 0;
let sseReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let syncPollTimer: ReturnType<typeof setInterval> | null = null;
let pollCursor: string | null = null;
let pollGeneration: NotificationGeneration | null = null;
let syncInFlight: Promise<void> | null = null;
let pendingSyncRequest: PendingSyncRequest | null = null;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

const isChannelId = (value: unknown): value is string =>
  typeof value === 'string'
  && value.length > 0
  && value.length <= MAX_CHANNEL_ID_LENGTH;

const parseBroadcastMessage = (value: unknown): NotificationBroadcastMessage | null => {
  if (!isRecord(value)) return null;

  if (value.type === 'state.request' && isChannelId(value.senderTabId)) {
    return { type: 'state.request', senderTabId: value.senderTabId };
  }

  if (
    value.type === 'sync'
    && isNotificationSyncPayload(value.payload)
    && (value.targetTabId === undefined || isChannelId(value.targetTabId))
  ) {
    return {
      type: 'sync',
      payload: value.payload,
      targetTabId: value.targetTabId,
    };
  }

  return null;
};

const getBroadcastChannel = () => {
  if (typeof BroadcastChannel === 'undefined') return null;
  if (!channel) {
    try {
      channel = new BroadcastChannel(BROADCAST_CHANNEL_NAME);
    } catch {
      channel = null;
    }
  }
  return channel;
};

const closeBroadcastChannel = () => {
  if (!channel) return;
  try { channel.close(); } catch { /* noop */ }
  channel = null;
};

const broadcastSyncPayload = (
  payload: NotificationSyncPayload,
  targetTabId?: string,
) => {
  const activeChannel = getBroadcastChannel();
  if (!activeChannel) return;
  try {
    activeChannel.postMessage({ type: 'sync', payload, targetTabId });
  } catch { /* noop */ }
};

const parseEventData = (value: unknown): unknown => {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
};

const extractEventValue = (data: SSEMessage['data'], key: 'cursor' | 'generation') => {
  let current: unknown = data;
  for (let depth = 0; depth < 2; depth += 1) {
    current = parseEventData(current);
    if (!isRecord(current)) return null;
    if (current[key] !== undefined) return current[key];
    current = current.data;
  }
  return null;
};

const extractEventCursor = (data: SSEMessage['data']): string | null => {
  const cursor = extractEventValue(data, 'cursor');
  return typeof cursor === 'string'
    && cursor.length > 0
    && cursor.length <= MAX_CURSOR_LENGTH
    ? cursor
    : null;
};

const extractEventGeneration = (
  data: SSEMessage['data'],
): NotificationGeneration | null => {
  const generation = extractEventValue(data, 'generation');
  return isNotificationGeneration(generation) ? generation : null;
};

const performNotificationSync = async (
  targetCursor: string | null,
  allowResponseCursor: boolean,
) => {
  try {
    const response: unknown = await notificationSync();
    if (!isNotificationSyncPayload(response)) return;

    const cursor = targetCursor
      ?? (allowResponseCursor && !sseConnected ? response.cursor : pollCursor ?? '');
    const payload = { ...response, cursor };
    bridge?.applySync(payload);
    broadcastSyncPayload(payload);
  } catch {
    // A later poll, SSE event, or reconnect will retry synchronization.
  }
};

const requestNotificationSync = (
  targetCursor: string | null = null,
  allowResponseCursor = false,
) => {
  if (!allowResponseCursor && targetCursor && targetCursor === pollCursor) {
    return syncInFlight ?? Promise.resolve();
  }

  if (syncInFlight) {
    if (targetCursor) {
      pendingSyncRequest = { targetCursor, allowResponseCursor: false };
    } else if (!pendingSyncRequest?.targetCursor) {
      pendingSyncRequest = {
        targetCursor: null,
        allowResponseCursor:
          allowResponseCursor || pendingSyncRequest?.allowResponseCursor === true,
      };
    }
    return syncInFlight;
  }

  syncInFlight = (async () => {
    let request: PendingSyncRequest | null = { targetCursor, allowResponseCursor };
    while (request) {
      await performNotificationSync(
        request.targetCursor,
        request.allowResponseCursor,
      );
      request = pendingSyncRequest;
      pendingSyncRequest = null;
    }
  })().finally(() => {
    syncInFlight = null;
  });

  return syncInFlight;
};

const GENERATION_EVENTS = new Set([
  'notification.published',
  'notification.withdrawn',
  'notification.expired',
]);
const USER_STATE_EVENTS = new Set(['unread.changed', 'banner.changed']);

const handleSSEMessages = (items: SSEMessage[]) => {
  let shouldSync = false;
  let targetCursor: string | null = null;

  items.forEach((message) => {
    if (message.event && USER_STATE_EVENTS.has(message.event)) {
      const eventCursor = extractEventCursor(message.data);
      if (eventCursor === null || eventCursor === pollCursor) return;
      shouldSync = true;
      targetCursor = eventCursor;
      return;
    }

    if (message.event === 'notification.sync' || message.event === 'sync') {
      shouldSync = true;
      return;
    }

    if (message.event && GENERATION_EVENTS.has(message.event)) {
      const eventGeneration = extractEventGeneration(message.data);
      if (eventGeneration !== null && eventGeneration !== pollGeneration) {
        shouldSync = true;
      }
    }
  });

  if (shouldSync) void requestNotificationSync(targetCursor);
};

const clearSSEReconnectTimer = () => {
  if (!sseReconnectTimer) return;
  clearTimeout(sseReconnectTimer);
  sseReconnectTimer = null;
};

const stopNotificationSyncPolling = () => {
  if (!syncPollTimer) return;
  clearInterval(syncPollTimer);
  syncPollTimer = null;
};

const pollWhileSSEDisconnected = () => {
  if (!realtimeEnabled || !isSSELeader || sseConnected) return;
  void requestNotificationSync(null, true);
};

const startNotificationSyncPolling = () => {
  if (!realtimeEnabled || !isSSELeader || sseConnected || syncPollTimer) return;
  pollWhileSSEDisconnected();
  syncPollTimer = setInterval(pollWhileSSEDisconnected, SYNC_POLL_INTERVAL_MS);
};

const stopSSEConnection = () => {
  sseConnected = false;
  sseConnectionId += 1;
  clearSSEReconnectTimer();
  stopNotificationSyncPolling();

  const abort = sseAbort;
  sseAbort = null;
  sseStarted = false;
  if (abort) {
    try { abort(); } catch { /* noop */ }
  }
};

function scheduleSSEReconnect() {
  clearSSEReconnectTimer();
  if (!realtimeEnabled || !isSSELeader) return;

  sseReconnectTimer = setTimeout(() => {
    sseReconnectTimer = null;
    if (isSSELeader) startSSEConnection();
  }, SSE_RECONNECT_DELAY_MS);
}

function handleServerShutdown(connectionId: number) {
  if (!realtimeEnabled || !isSSELeader || connectionId !== sseConnectionId) return;

  sseConnected = false;
  sseConnectionId += 1;
  const abort = sseAbort;
  sseAbort = null;
  sseStarted = false;
  clearSSEReconnectTimer();
  if (abort) {
    try { abort(); } catch { /* noop */ }
  }
  startNotificationSyncPolling();
  scheduleSSEReconnect();
}

function startSSEConnection() {
  if (!realtimeEnabled || !isSSELeader || sseStarted || sseAbort) return;

  clearSSEReconnectTimer();
  const connectionId = ++sseConnectionId;
  sseStarted = true;

  void notificationsEvents(
    (messages) => {
      if (!realtimeEnabled || !isSSELeader || connectionId !== sseConnectionId) return;
      handleSSEMessages(messages);
      if (messages.some((message) => message.event === 'server.shutdown')) {
        handleServerShutdown(connectionId);
      }
    },
    (abort) => {
      if (isSSELeader && connectionId === sseConnectionId) sseAbort = abort;
    },
    () => {
      if (realtimeEnabled && isSSELeader && connectionId === sseConnectionId) {
        sseConnected = true;
        stopNotificationSyncPolling();
      }
    },
  ).catch(() => undefined).finally(() => {
    if (connectionId !== sseConnectionId) return;
    sseConnected = false;
    sseStarted = false;
    sseAbort = null;
    startNotificationSyncPolling();
    scheduleSSEReconnect();
  });
}

const becomeSSELeaderWithoutLock = () => {
  if (!realtimeEnabled || isSSELeader) return;
  isSSELeader = true;
  sseConnected = false;
  startNotificationSyncPolling();
  startSSEConnection();
};

const startSSELeaderElection = () => {
  if (!realtimeEnabled || isSSELeader || leaderElectionStarted) return;

  const lockManager = typeof navigator !== 'undefined' ? navigator.locks : undefined;
  if (!lockManager) {
    becomeSSELeaderWithoutLock();
    return;
  }

  leaderElectionStarted = true;
  const controller = new AbortController();
  leaderElectionController = controller;

  void lockManager.request(
    SSE_LEADER_LOCK_NAME,
    { mode: 'exclusive', signal: controller.signal },
    async () => {
      if (!realtimeEnabled || controller.signal.aborted) return;

      isSSELeader = true;
      sseConnected = false;
      let releaseLeadership: () => void = () => undefined;
      const leadershipLifetime = new Promise<void>((resolve) => {
        releaseLeadership = resolve;
      });
      releaseSSELeadership = releaseLeadership;

      startNotificationSyncPolling();
      startSSEConnection();

      try {
        await leadershipLifetime;
      } finally {
        if (releaseSSELeadership === releaseLeadership) releaseSSELeadership = null;
        if (isSSELeader) {
          isSSELeader = false;
          stopSSEConnection();
        }
      }
    },
  ).catch((error: unknown) => {
    if (
      realtimeEnabled
      && (!(error instanceof DOMException) || error.name !== 'AbortError')
    ) becomeSSELeaderWithoutLock();
  }).finally(() => {
    if (leaderElectionController === controller) leaderElectionController = null;
    leaderElectionStarted = false;
    if (realtimeEnabled && !isSSELeader) startSSELeaderElection();
  });
};

export const configureNotificationRealtime = (nextBridge: RealtimeBridge) => {
  bridge = nextBridge;
};

export const updateNotificationRealtimeState = (
  cursor: string | null,
  generation: NotificationGeneration | null,
) => {
  pollCursor = cursor;
  pollGeneration = generation;
};

export const setupNotificationRealtime = () => {
  realtimeEnabled = true;
  const activeChannel = getBroadcastChannel();
  if (activeChannel) {
    activeChannel.onmessage = (event: MessageEvent<unknown>) => {
      const message = parseBroadcastMessage(event.data);
      if (!message) return;

      if (
        message.type === 'sync'
        && (!message.targetTabId || message.targetTabId === TAB_ID)
      ) {
        bridge?.applySync(message.payload);
        return;
      }

      if (message.type === 'state.request' && isSSELeader) {
        const snapshot = bridge?.getSnapshot();
        if (snapshot && isNotificationSyncPayload(snapshot)) {
          broadcastSyncPayload(snapshot, message.senderTabId);
        }
      }
    };
    activeChannel.postMessage({ type: 'state.request', senderTabId: TAB_ID });
  }
  startSSELeaderElection();
};

export const teardownNotificationRealtime = () => {
  realtimeEnabled = false;
  if (leaderElectionController) {
    try { leaderElectionController.abort(); } catch { /* noop */ }
  }

  const releaseLeadership = releaseSSELeadership;
  releaseSSELeadership = null;
  if (releaseLeadership) releaseLeadership();

  isSSELeader = false;
  stopSSEConnection();
  closeBroadcastChannel();
};
