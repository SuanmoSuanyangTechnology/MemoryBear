import { message } from 'antd';
import i18n from '@/i18n'
import { cookieUtils } from './request'
const API_PREFIX = '/api'

export interface SSEMessage {
  event?: string
  data?: string | object
}
export function parseSSEToJSON(sseString: string) {
  const events: SSEMessage[] = []
  const lines = sseString.trim().split('\n')
  
  let currentEvent: SSEMessage = {}
  
  try {
    for (const line of lines) {
      if (line.startsWith('event:')) {
        if (Object.keys(currentEvent).length > 0) {
          events.push(currentEvent)
          currentEvent = {}
        }
        currentEvent.event = line.substring(6).trim()
      } else if (line.startsWith('data:')) {
        const dataStr = line.substring(5).trim()
        try {
          currentEvent.data = JSON.parse(dataStr.replace(/"/g, '"'))
        } catch {
          currentEvent.data = dataStr
        }
      }
    }
    
    if (Object.keys(currentEvent).length > 0) {
      events.push(currentEvent)
    }
    
    return events
  } catch (error) {
    console.error('Parse stream error:', error)
    return []
  }
}


export const handleSSE = async (url: string, data: any, onMessage?: (data: SSEMessage[]) => void, config = { headers: {} }) => {
  try {
    const token = cookieUtils.get('authToken');
    const response = await fetch(`${API_PREFIX}${url}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        ...config.headers,
      },
      body: JSON.stringify(data)
    });

    const { status } = response

    switch(status) {
      case 401:
        if (url?.includes('/public')) {
          return message.warning(i18n.t('common.publicApiCannotRefreshToken'));
        }
        window.location.href = `/#/login`;
        break;
      default:
        if (!response.body) throw new Error('No response body');
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          
          const chunk = decoder.decode(value, { stream: true });
          if (onMessage) {
            onMessage(parseSSEToJSON(chunk) ?? {});
          }
        }
        break;
    }
  } catch (error) {
    console.error('Request failed:', error);
    throw error;
  }
}