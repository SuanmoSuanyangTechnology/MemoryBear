import { message } from 'antd';
import i18n from '@/i18n'
import { cookieUtils } from './request'
const API_PREFIX = '/api'

export const handleSSE = async (url: string, data: any, onMessage?: (data: string) => void, config = {}) => {
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
            onMessage(chunk);
          }
        }
        break;
    }
  } catch (error) {
    console.error('Request failed:', error);
    throw error;
  }
}