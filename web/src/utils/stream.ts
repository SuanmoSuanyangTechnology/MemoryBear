/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:35:43 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:35:43 
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

import { message } from 'antd';
import i18n from '@/i18n'
import { cookieUtils } from './request'
import { refreshToken } from '@/api/user'
import { clearAuthData } from './auth'
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
}

/**
 * Parse SSE string format to JSON objects
 * @param sseString - Raw SSE string data
 * @returns Array of parsed SSE messages
 */
export function parseSSEToJSON(sseString: string) {
  const events: SSEMessage[] = []
  const lines = sseString.trim().split('\n')
  
  let currentEvent: SSEMessage = {}
  let dataContent = ''
  
  for (const line of lines) {
    if (line.startsWith('event:')) {
      if (currentEvent.event && dataContent) {
        currentEvent.data = parseDataContent(dataContent)
        events.push(currentEvent)
      }
      currentEvent = { event: line.substring(6).trim() }
      dataContent = ''
    } else if (line.startsWith('data:')) {
      if (dataContent) dataContent += '\n'
      dataContent += line.substring(5).trim()
    }
  }

  
  if (currentEvent.event && dataContent) {
    currentEvent.data = parseDataContent(dataContent)
    console.log('currentEvent', currentEvent)
    events.push(currentEvent)
  }
  
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
 * Make SSE request with authentication
 * @param url - API endpoint
 * @param data - Request payload
 * @param token - Authentication token
 * @param config - Additional request configuration
 * @returns Fetch response
 */
const makeSSERequest = async (url: string, data: any, token: string, config = { headers: {} }) => {
  return fetch(`${API_PREFIX}${url}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
      ...config.headers,
    },
    body: JSON.stringify(data)
  });
};

/**
 * Handle SSE stream with automatic token refresh and message parsing
 * @param url - API endpoint
 * @param data - Request payload
 * @param onMessage - Callback for each parsed message
 * @param config - Additional request configuration
 */
export const handleSSE = async (url: string, data: any, onMessage?: (data: SSEMessage[]) => void, config = { headers: {} }) => {
  try {
    let token = cookieUtils.get('authToken');
    let response = await makeSSERequest(url, data, token || '', config);

    switch (response.status) {
      case 500:
      case 502:
        const errorData = await response.json();
        errorData.error || i18n.t('common.serviceUpgrading');
        message.warning(errorData.error || i18n.t('common.serviceUpgrading'));
        break
      case 400:
        const error = await response.json();
        message.warning(error.error);
        throw error || 'Bad Request';
      case 504:
        const errorJson = await response.json();
        message.warning(errorJson.error || i18n.t('common.serverError'));
        break
      case 401:
        if (url?.includes('/public')) {
          return message.warning(i18n.t('common.publicApiCannotRefreshToken'));
        }
        try {
          const newToken = await refreshTokenForSSE();
          response = await makeSSERequest(url, data, newToken, config);
        } catch (refreshError) {
          return;
        }
        break;
    }
    if (!response.body) throw new Error('No response body');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = ''; // Buffer for handling incomplete messages

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

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
    if (buffer.trim() && onMessage) {
      onMessage(parseSSEToJSON(buffer) ?? {});
    }
  } catch (error) {
    console.error('Request failed:', error);
    throw error;
  }
};