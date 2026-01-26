import { message } from 'antd';
import i18n from '@/i18n'
import { cookieUtils } from './request'
import { refreshToken } from '@/api/user'
import { clearAuthData } from './auth'
const API_PREFIX = '/api'

// Token refresh state
let isRefreshing = false;
let refreshPromise: Promise<string> | null = null;

// Refresh token function for SSE
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

export interface SSEMessage {
  event?: string
  data?: string | object
}
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

function parseDataContent(dataContent: string): string | object {
  try {
    // 第一层解码：HTML实体
    let unescaped = dataContent
      .replace(/&quot;/g, '"')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&#39;/g, "'")
    
    // 解析第一层JSON
    const firstParse = JSON.parse(unescaped)
    
    // 如果data字段是字符串且包含JSON，解析data层但保持chunk为字符串
    if (firstParse.data && typeof firstParse.data === 'string' && firstParse.data.includes("{")) {
      try {
        firstParse.data = JSON.parse(firstParse.data)
      } catch {
        // 保持原字符串
      }
    }
    
    return firstParse
  } catch {
    return dataContent
  }
}

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
    let buffer = ''; // 添加缓冲区来处理不完整的消息

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      buffer += chunk;

      // 处理完整的事件
      const events = buffer.split('\n\n');
      buffer = events.pop() || ''; // 保留最后一个可能不完整的事件

      for (const event of events) {
        if (event.trim() && onMessage) {
          onMessage(parseSSEToJSON(event) ?? {});
        }
      }
    }

    // 处理剩余的缓冲区内容
    if (buffer.trim() && onMessage) {
      onMessage(parseSSEToJSON(buffer) ?? {});
    }
  } catch (error) {
    console.error('Request failed:', error);
    throw error;
  }
};