/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:35:43 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-01 15:17:24
 */
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
import { cookieUtils } from './request';
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

  return fetch(requestUrl, {
    method,
    headers: {
      ...(method === 'POST' ? { 'Content-Type': 'application/json' } : {}),
      'Authorization': `Bearer ${token}`,
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

    switch (response.status) {
      case 500:
      case 502:
        const errorData = await response.json();
        const errorInfo = errorData.error || errorData.msg || i18n.t('common.serviceUpgrading');
        message.warning(errorInfo);
        throw new Error(JSON.stringify(errorData));
      case 400:
        const error = await response.json();
        const error400 = error.error || error.msg || 'Bad Request';
        message.warning(error400);
        throw new Error(JSON.stringify(error));
      case 403:
        const errors = await response.json();
        message.warning(i18n.t('common.permissionDenied'));
        throw new Error(JSON.stringify(errors));
      case 504:
        const errorJson = await response.json();
        const errorMsg = errorJson.error || errorJson.msg || i18n.t('common.serverError');
        message.warning(errorMsg);
        throw new Error(JSON.stringify(errorJson));
      case 401:
        if (url?.includes('/public')) {
          return message.warning(i18n.t('common.publicApiCannotRefreshToken'));
        }
        try {
          const newToken = await refreshTokenForSSE();
          response = await makeSSERequest(url, data, newToken, config, controller.signal);
        } catch (refreshError) {
          return;
        }
        break;
      default:
        if (!response.ok) {
          const defaultData = await response.json().catch(() => ({}));
          const defaultMsg = defaultData.error || defaultData.msg;
          if (defaultMsg) message.warning(defaultMsg);
          throw new Error(defaultMsg || `HTTP ${response.status}`);
        }
    }
    if (!response.body) throw new Error('No response body');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = ''; // Buffer for handling incomplete messages

    try {
      while (true) {
        const { done, value } = await reader.read();
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
      reader.cancel();
    }
  } catch (error: any) {
    if (error?.name !== 'AbortError') {
      console.error('Request failed:', error);
      throw error;
    }
  }

};