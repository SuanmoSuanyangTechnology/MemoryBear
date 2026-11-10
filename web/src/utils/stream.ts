/**
 * Server-Sent Events (SSE) Stream Utility Module
 * 
 * Provides SSE handling with:
 * - Automatic token refresh on 401 errors
 * - SSE message parsing and JSON decoding
 * - HTML entity decoding
 * - Stream buffering for incomplete messages
 * 
 * @module stream
 */

import { refreshToken } from '@/api/user';
import i18n from '@/i18n';
import { message } from 'antd';
import { clearAuthData } from './auth';
import { cookieUtils, setLastTraceId, setLastRetryAfter } from './request';
const API_PREFIX = '/api'

// Token refresh state
let isRefreshing = false;
let refreshPromise: Promise<string> | null = null;

/**
 * Refresh authentication token for SSE requests
 * @returns New access token
 */
const refreshTokenForSSE = async (): Promise<string> => {
  if (isRefreshing && refreshPromise) {
    return refreshPromise;
  }
  
  isRefreshing = true;
  refreshPromise = (async () => {
    try {
      const refresh_token = cookieUtils.get('refreshToken');
      if (!refresh_token) {
        throw new Error(i18n.t('common.refreshTokenNotExist'));
      }
      const response: any = await refreshToken();
      cookieUtils.set('refreshToken', response.refresh_token);
      const newToken = response.access_token;
      cookieUtils.set('authToken', newToken);
      return newToken;
    } catch (error) {
      clearAuthData();
      message.warning(i18n.t('common.loginExpired'));
      if (!window.location.hash.includes('#/login')) {
        window.location.href = `/#/login`;
      }
      throw error;
    } finally {
      isRefreshing = false;
      refreshPromise = null;
    }
  })();
  
  return refreshPromise;
};

/**
 * SSE message structure
 */
export interface SSEMessage {
  event?: string
  data?: string | object
  /**
   * SSE comment line (starts with `:`).
   * Commonly used for server-sent heartbeats such as `: heartbeat` or `: connected`.
   */
  comment?: string
}

/**
 * SSE error frame structure (unified error handling v0.5.5)
 */
export interface SSEErrorFrame {
  code: number
  msg: string
  error: string
  message?: string
  attempts: number
  channel_id?: string | null
  retryable: boolean
  retry_after_seconds?: number
  time: number
}

/**
 * Parse SSE string format to JSON objects
 *
 * Handles the following SSE line types:
 * - `event: <name>`   — event type tag
 * - `data: <payload>` — JSON / string payload (possibly multi-line)
 * - `: <comment>`     — comment line (e.g. heartbeat / connected). Emitted as a
 *                       message with the `comment` field set.
 *
 * @param sseString - Raw SSE string data (one or more events separated by `\n\n`)
 * @returns Array of parsed SSE messages
 */
export function parseSSEToJSON(sseString: string) {
  const events: SSEMessage[] = []
  const lines = sseString.trim().split('\n')

  const flushCurrent = (currentEvent: SSEMessage, dataContent: string) => {
    if (currentEvent.event || dataContent) {
      const evt: SSEMessage = { ...currentEvent }
      if (dataContent) evt.data = parseDataContent(dataContent)
      events.push(evt)
      return true
    }
    return false
  }

  let currentEvent: SSEMessage = {}
  let dataContent = ''
  console.log('lines', sseString)

  for (const line of lines) {
    // SSE comment line (`: heartbeat`, `: connected`, ...) — emit as a comment event.
    if (line.startsWith(':')) {
      flushCurrent(currentEvent, dataContent)
      currentEvent = {}
      dataContent = ''
      const comment = line.slice(1).trim()
      if (comment) events.push({ comment })
      continue
    }

    if (line.startsWith('event:')) {
      flushCurrent(currentEvent, dataContent)
      currentEvent = { event: line.substring(6).trim() }
      dataContent = ''
    } else if (line.startsWith('data:')) {
      if (dataContent) dataContent += '\n'
      dataContent += line.substring(5).trim()
    }
  }

  flushCurrent(currentEvent, dataContent)
  console.log('events', events)
  return events
}

/**
 * Parse SSE data content with HTML entity decoding
 * @param dataContent - Raw data content string
 * @returns Parsed object or original string
 */
function parseDataContent(dataContent: string): string | object {
  try {
    // First layer: HTML entity decoding
    let unescaped = dataContent
      .replace(/&quot;/g, '"')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&#39;/g, "'")
    
    // Parse first layer JSON
    const firstParse = JSON.parse(unescaped)
    
    // If data field is a string containing JSON, parse data layer but keep chunk as string
    if (firstParse.data && typeof firstParse.data === 'string' && firstParse.data.includes("{")) {
      try {
        firstParse.data = JSON.parse(firstParse.data)
      } catch {
        // Keep original string
      }
    }
    
    return firstParse
  } catch {
    return dataContent
  }
}

/**
 * SSE request configuration
 */
export interface SSERequestConfig {
  headers?: Record<string, string>
  method?: 'GET' | 'POST'
  onOpen?: () => void
}

/**
 * Append request data to URL query parameters.
 * Arrays are represented as repeated parameters and objects are JSON encoded.
 */
const appendSearchParam = (params: URLSearchParams, key: string, value: unknown) => {
  if (value === undefined) return

  if (Array.isArray(value)) {
    value.forEach(item => appendSearchParam(params, key, item))
    return
  }

  if (value !== null && typeof value === 'object') {
    params.append(key, JSON.stringify(value))
    return
  }

  params.append(key, value === null ? '' : String(value))
}

const buildSSERequestUrl = (url: string, data: any) => {
  const params = new URLSearchParams()

  if (data instanceof URLSearchParams) {
    data.forEach((value, key) => params.append(key, value))
  } else if (data && typeof data === 'object') {
    Object.entries(data).forEach(([key, value]) => appendSearchParam(params, key, value))
  }

  const query = params.toString()
  if (!query) return `${API_PREFIX}${url}`

  return `${API_PREFIX}${url}${url.includes('?') ? '&' : '?'}${query}`
}

/**
 * Make SSE request with authentication
 * @param url - API endpoint
 * @param data - Request body for POST or query parameters for GET
 * @param token - Authentication token
 * @param config - Additional request configuration
 * @returns Fetch response
 */
const makeSSERequest = async (
  url: string,
  data: any,
  token: string,
  config: SSERequestConfig = {},
  signal?: AbortSignal,
) => {
  const method = config.method ?? 'POST'
  const requestUrl = method === 'GET' ? buildSSERequestUrl(url, data) : `${API_PREFIX}${url}`

  const language = localStorage.getItem('language') || 'en'

  return fetch(requestUrl, {
    method,
    headers: {
      ...(method === 'POST' ? { 'Content-Type': 'application/json' } : {}),
      'Authorization': `Bearer ${token}`,
      'X-Language-Type': language,
      ...config.headers,
    },
    body: method === 'POST' ? JSON.stringify(data) : undefined,
    signal,
  });
};

/**
 * Handle SSE stream with automatic token refresh and message parsing
 * @param url - API endpoint
 * @param data - Request body for POST or query parameters for GET
 * @param onMessage - Callback for each parsed message
 * @param config - Additional request configuration; defaults to POST
 */
export const handleSSE = async (
  url: string,
  data: any,
  onMessage?: (data: SSEMessage[]) => void,
  config: SSERequestConfig = {},
  onAbort?: (abort: () => void) => void,
) => {
  const controller = new AbortController();
  const abort = () => controller.abort();
  onAbort?.(abort);

  try {
    let token = cookieUtils.get('authToken');
    let response = await makeSSERequest(url, data, token || '', config, controller.signal);
    let hasRetried = false;

    const sseTraceId = response.headers.get('x-trace-id') || response.headers.get('X-Trace-Id')
    if (sseTraceId) setLastTraceId(sseTraceId)

    // Loop to handle 401, support unlimited retries (except for /public paths)
    while (response.status === 401 && !url?.includes('/public')) {
      const errorResponse = await response.json().catch(() => ({}));
      const errorCode = errorResponse.code;
      
      // Check errorCode on first 401
      if (!hasRetried) {
        if (errorCode === 2001) {
          clearAuthData();
          message.warning(errorResponse.msg);
          if (!window.location.hash.includes('#/login')) {
            window.location.href = `/#/login`;
          }
          return;
        }
        if (errorCode === 2002) {
          if (onMessage) {
            onMessage([{ event: 'error', data: { msg: errorResponse.msg || i18n.t('common.invalidCredentials'), error: errorResponse.error || '', code: 2002 } }])
          }
          return;
        }
      }
      
      // Refresh token and retry
      try {
        const newToken = await refreshTokenForSSE();
        response = await makeSSERequest(url, data, newToken, config, controller.signal);
        hasRetried = true;
        
        const retryTraceId = response.headers.get('x-trace-id') || response.headers.get('X-Trace-Id')
        if (retryTraceId) setLastTraceId(retryTraceId)
      } catch (refreshError) {
        return;
      }
    }

    // Handle 401 for /public paths, pass error message via onMessage callback
    if (response.status === 401 && url?.includes('/public')) {
      const errorResponse = await response.json().catch(() => ({}));
      if (onMessage) {
        onMessage([{ event: 'error', data: { msg: errorResponse.msg || i18n.t('common.loginRequired'), error: errorResponse.error || '', code: errorResponse.code || 401 } }])
      }
      return;
    }

    switch (response.status) {
      case 429:
      case 503: {
        const retryAfterHeader = response.headers.get('retry-after') || response.headers.get('Retry-After')
        if (retryAfterHeader) {
          const seconds = parseInt(retryAfterHeader, 10)
          if (Number.isFinite(seconds) && seconds > 0) setLastRetryAfter(seconds)
        }
        const errorData = await response.json().catch(() => ({}))
        const errorMsg = errorData.msg || (response.status === 429 ? i18n.t('common.tooManyRequests') : i18n.t('common.serviceUpgrading'))
        if (onMessage) {
          onMessage([{ event: 'error', data: { msg: errorMsg, error: errorData.error || '', code: errorData.code || (response.status === 429 ? 10004 : 10003) } }])
        }
        return
      }
      case 500:
      case 502: {
        const errorData = await response.json().catch(() => ({}));
        const errorInfo = errorData.msg || i18n.t('common.serviceUpgrading');
        if (onMessage) {
          onMessage([{ event: 'error', data: { msg: errorInfo, error: errorData.error || '', code: errorData.code || 10001 } }])
        }
        return
      }
      case 400: {
        const error = await response.json().catch(() => ({}));
        const error400 = error.msg || 'Bad Request';
        if (onMessage) {
          onMessage([{ event: 'error', data: { msg: error400, error: error.error || '', code: error.code || 1003 } }])
        }
        return
      }
      case 403: {
        const errors = await response.json().catch(() => ({}));
        const errorMsg403 = errors.msg || i18n.t('common.permissionDenied');
        if (onMessage) {
          onMessage([{ event: 'error', data: { msg: errorMsg403, error: errors.error || '', code: errors.code || 3001 } }])
        }
        return
      }
      case 504: {
        const errorJson = await response.json().catch(() => ({}));
        const errorMsg504 = errorJson.msg || i18n.t('common.serverError');
        if (onMessage) {
          onMessage([{ event: 'error', data: { msg: errorMsg504, error: errorJson.error || '', code: errorJson.code || 10001 } }])
        }
        return
      }
      default:
        if (!response.ok) {
          const defaultData = await response.json().catch(() => ({}));
          const defaultMsg = defaultData.msg || `HTTP ${response.status}`;
          if (onMessage) {
            onMessage([{ event: 'error', data: { msg: defaultMsg, error: defaultData.error || '', code: defaultData.code || response.status } }])
          }
          return
        }
    }
    if (!response.body) throw new Error('No response body');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = ''; // Buffer for handling incomplete messages

    try {
      while (true) {
        const { done, value } = await reader.read().catch((err) => {
          if (err?.name === 'AbortError') return { done: true, value: undefined }
          throw err
        });
        if (done || controller.signal.aborted) break;

        const chunk = decoder.decode(value, { stream: true });
        buffer += chunk;

        // Process complete events
        const events = buffer.split('\n\n');
        buffer = events.pop() || ''; // Keep last potentially incomplete event

        for (const event of events) {
          if (event.trim() && onMessage) {
            onMessage(parseSSEToJSON(event) ?? {});
          }
        }
      }

      // Process remaining buffer content
      if (!controller.signal.aborted && buffer.trim() && onMessage) {
        onMessage(parseSSEToJSON(buffer) ?? {});
      }
    } finally {
      reader.cancel().catch(() => {})
    }
  } catch (error: any) {
    if (error?.name !== 'AbortError') {
      console.error('Request failed:', error);
      throw error;
    }
  }

};