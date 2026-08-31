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
import { createSSESyncScheduler } from './sseSyncScheduler';

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
const MAX_CHANNEL_ID_LENGTH = 256;
const MAX_CURSOR_LENGTH = 4_096;

/** Delay before reconnecting after a graceful `server.shutdown` event. */
const SSE_RECONNECT_DELAY_MS = 30_000;
/** Interval for the notification sync polling fallback (SSE disconnected). */
const SYNC_POLL_INTERVAL_MS = 30_000;
/** Window used to coalesce bursts of SSE events into one sync request. */
const SSE_SYNC_BATCH_WINDOW_MS = 3_000;
/**
 * SSE heartbeat watchdog window.
 *
 * The server emits a comment line (`: heartbeat`) roughly every 60s.
 * If nothing has been received for ~2.5× that window the stream is treated as
 * silently dead (half-open TCP / stuck proxy) and forcefully reconnected.
 */
const SSE_HEARTBEAT_WATCHDOG_MS = 150_000;
/**
 * Reconnect delay when the heartbeat watchdog fires.
 * Users have already waited the full watchdog window, so we reconnect fast.
 */
const SSE_HEARTBEAT_RECONNECT_DELAY_MS = 2_000;

const TAB_ID = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
  ? crypto.randomUUID()
  : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

interface PendingSyncRequest {
  targetCursor: string | null;
  allowResponseCursor: boolean;
}

type FailureReason = 'server.shutdown' | 'heartbeat.timeout';
type CommentName = 'heartbeat' | 'connected';

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
let syncRetryBlockedUntil = 0;
let heartbeatWatchdogTimer: ReturnType<typeof setTimeout> | null = null;

/* -------------------------------------------------------------------------- */
/*                               Type predicates                              */
/* -------------------------------------------------------------------------- */

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

const isChannelId = (value: unknown): value is string =>
  typeof value === 'string'
  && value.length > 0
  && value.length <= MAX_CHANNEL_ID_LENGTH;

/**
 * True when `connectionId` matches the current SSE session AND this tab is the
 * elected SSE leader AND realtime has been enabled.
 *
 * Use this guard everywhere before mutating connection-scoped state so stale
 * callbacks from retired connections are a no-op.
 */
const isActiveConnection = (connectionId: number): boolean =>
  realtimeEnabled && isSSELeader && connectionId === sseConnectionId;

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

const hasComment = (messages: SSEMessage[], name: CommentName): boolean =>
  messages.some((message) => message.comment === name);

/* -------------------------------------------------------------------------- */
/*                          BroadcastChannel helpers                          */
/* -------------------------------------------------------------------------- */

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

/* -------------------------------------------------------------------------- */
/*                          SSE event-data extraction                         */
/* -------------------------------------------------------------------------- */

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

/* -------------------------------------------------------------------------- */
/*                          notificationSync orchestration                    */
/* -------------------------------------------------------------------------- */

const isNotificationSyncRetryBlocked = () => Date.now() < syncRetryBlockedUntil;

const performNotificationSync = async (
  targetCursor: string | null,
  allowResponseCursor: boolean,
): Promise<boolean> => {
  try {
    const response: unknown = await notificationSync();
    if (!isNotificationSyncPayload(response)) {
      syncRetryBlockedUntil = Date.now() + SYNC_POLL_INTERVAL_MS;
      return false;
    }

    const cursor = targetCursor
      ?? (allowResponseCursor && !sseConnected ? response.cursor : pollCursor ?? '');
    const payload = { ...response, cursor };
    bridge?.applySync(payload);
    broadcastSyncPayload(payload);
    syncRetryBlockedUntil = 0;
    return true;
  } catch {
    syncRetryBlockedUntil = Date.now() + SYNC_POLL_INTERVAL_MS;
    // Wait for the next polling window instead of retrying immediately.
    return false;
  }
};

const requestNotificationSync = (
  targetCursor: string | null = null,
  allowResponseCursor = false,
) => {
  if (!allowResponseCursor && isNotificationSyncRetryBlocked()) {
    return syncInFlight ?? Promise.resolve();
  }

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
      const synchronized = await performNotificationSync(
        request.targetCursor,
        request.allowResponseCursor,
      );
      if (!synchronized) {
        pendingSyncRequest = null;
        break;
      }
      request = pendingSyncRequest;
      pendingSyncRequest = null;
    }
  })().finally(() => {
    syncInFlight = null;
  });

  return syncInFlight;
};

const sseSyncScheduler = createSSESyncScheduler(
  SSE_SYNC_BATCH_WINDOW_MS,
  (targetCursor) => {
    if (!realtimeEnabled || !isSSELeader || isNotificationSyncRetryBlocked()) return;
    void requestNotificationSync(targetCursor);
  },
);

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

  if (shouldSync && !isNotificationSyncRetryBlocked()) {
    sseSyncScheduler.schedule(targetCursor);
  }
};

/* -------------------------------------------------------------------------- */
/*                          Timer / poll lifecycle                            */
/* -------------------------------------------------------------------------- */

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

const clearHeartbeatWatchdog = () => {
  if (!heartbeatWatchdogTimer) return;
  clearTimeout(heartbeatWatchdogTimer);
  heartbeatWatchdogTimer = null;
};

/**
 * Reset the heartbeat watchdog after any server activity (comment or event).
 * If it fires, `handleServerShutdown` is invoked with `heartbeat.timeout` so
 * the aggressive reconnect + sync policy kicks in.
 */
const kickHeartbeatWatchdog = (connectionId: number) => {
  clearHeartbeatWatchdog();
  heartbeatWatchdogTimer = setTimeout(() => {
    heartbeatWatchdogTimer = null;
    if (isActiveConnection(connectionId) && sseConnected) {
      handleServerShutdown(connectionId, 'heartbeat.timeout');
    }
  }, SSE_HEARTBEAT_WATCHDOG_MS);
};

/* -------------------------------------------------------------------------- */
/*                          SSE connection lifecycle                          */
/* -------------------------------------------------------------------------- */

const markSSEConnected = (connectionId: number) => {
  if (!isActiveConnection(connectionId)) return;
  if (sseConnected) return;
  sseConnected = true;
  stopNotificationSyncPolling();
};

/**
 * Shared teardown steps: abort the stream, clear timers/watches, bump the
 * connection id so any pending callbacks become stale.
 *
 * Callers layer on the *recovery* policy (polling + reconnect delay) on top.
 */
const teardownSSERuntime = () => {
  sseConnected = false;
  sseConnectionId += 1;
  sseStarted = false;
  clearSSEReconnectTimer();
  clearHeartbeatWatchdog();
  sseSyncScheduler.clear();

  const abort = sseAbort;
  sseAbort = null;
  if (abort) {
    try { abort(); } catch { /* noop */ }
  }
};

const stopSSEConnection = () => {
  teardownSSERuntime();
  stopNotificationSyncPolling();
};

const scheduleSSEReconnect = (delayMs: number = SSE_RECONNECT_DELAY_MS) => {
  clearSSEReconnectTimer();
  if (!realtimeEnabled || !isSSELeader) return;

  sseReconnectTimer = setTimeout(() => {
    sseReconnectTimer = null;
    if (isSSELeader) startSSEConnection();
  }, delayMs);
};

/**
 * Teardown the active SSE stream and decide how aggressively to recover.
 *
 * - `heartbeat.timeout` → immediate sync + fast SSE reconnect (2s).
 *   Users have already waited the full watchdog window.
 * - `server.shutdown`  → sync fallback polling + graceful 30s reconnect.
 */
function handleServerShutdown(
  connectionId: number,
  reason: FailureReason = 'server.shutdown',
) {
  if (!isActiveConnection(connectionId)) return;

  teardownSSERuntime();
  startNotificationSyncPolling();

  if (reason === 'heartbeat.timeout') {
    // startNotificationSyncPolling already fired pollWhileSSEDisconnected()
    // which runs requestNotificationSync(null, true) once immediately, so no
    // second fire is required here.
    scheduleSSEReconnect(SSE_HEARTBEAT_RECONNECT_DELAY_MS);
  } else {
    scheduleSSEReconnect();
  }
}

function startSSEConnection() {
  if (!realtimeEnabled || !isSSELeader || sseStarted || sseAbort) return;

  clearSSEReconnectTimer();
  const connectionId = ++sseConnectionId;
  sseStarted = true;

  void notificationsEvents(
    (messages) => {
      if (!isActiveConnection(connectionId)) return;

      const hasHeartbeat = hasComment(messages, 'heartbeat');
      const hasConnected = hasComment(messages, 'connected');
      const hasAnyActivity = hasHeartbeat || hasConnected || messages.length > 0;

      if (hasAnyActivity) kickHeartbeatWatchdog(connectionId);
      if (hasHeartbeat || hasConnected) markSSEConnected(connectionId);

      handleSSEMessages(messages);
      if (messages.some((message) => message.event === 'server.shutdown')) {
        handleServerShutdown(connectionId, 'server.shutdown');
      }
    },
    (abort) => {
      if (isActiveConnection(connectionId)) sseAbort = abort;
    },
    () => {
      markSSEConnected(connectionId);
    },
  ).catch(() => undefined).finally(() => {
    if (connectionId !== sseConnectionId) return;
    teardownSSERuntime();
    startNotificationSyncPolling();
    scheduleSSEReconnect();
  });
}

/* -------------------------------------------------------------------------- */
/*                          Leader election (Web Locks)                       */
/* -------------------------------------------------------------------------- */

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

/* -------------------------------------------------------------------------- */
/*                              Public API                                    */
/* -------------------------------------------------------------------------- */

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
