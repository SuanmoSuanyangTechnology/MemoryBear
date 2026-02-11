/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:35:15 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:35:15 
 */
/**
 * HTTP Request Utility Module
 * 
 * Provides axios-based HTTP client with:
 * - Automatic token refresh on 401 errors
 * - Request/response interceptors
 * - Cookie-based authentication
 * - Error handling and user notifications
 * - File upload/download support
 * 
 * @module request
 */

import axios from 'axios';
import type { AxiosRequestConfig } from 'axios';
import { clearAuthData } from './auth';
import { message } from 'antd';
import { refreshTokenUrl, refreshToken, loginUrl, logoutUrl } from '@/api/user'
import i18n from '@/i18n'

/**
 * Standard API response structure
 */
export interface ResponseData {
  code: number;
  msg: string;
  data: data | Record<string, string | number | boolean | object | null | undefined>[] | object | any[];
  error: string;
  time: number;
}

/**
 * Paginated data structure
 */
interface data {
  "items": Record<string, string | number | boolean | object | null | undefined>[];
  "page": {
    "page": number;
    "pagesize": number;
    "total": number;
    "hasnext": boolean;
  }
}

export const API_PREFIX = '/api'

// Create axios instance
const service = axios.create({
  baseURL: API_PREFIX, // Corresponds to proxy config in vite.config.ts
  // timeout: 10000, // Request timeout
  withCredentials: false,
  headers: {
    'Content-Type': 'application/json'
  },
});

// Token refresh state
let isRefreshing = false;

// Queue for pending requests during token refresh
interface RequestQueueItem {
  config: AxiosRequestConfig;
  resolve: (token: string) => void;
  reject: (error: Error) => void;
}
let requests: RequestQueueItem[] = [];

// Request interceptor
service.interceptors.request.use(
  (config) => {
    if (!config.headers.Authorization) {
      const token = cookieUtils.get('authToken');
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    }
    const language = localStorage.getItem('language')
    config.headers['X-Language-Type'] = language || 'en';
    config.headers.Cookie = undefined
    return config;
  },
  (error) => {
    // Handle request errors
    console.error('Request error:', error);
    return Promise.reject(error);
  }
);

/**
 * Refresh authentication token
 * @returns New access token
 */
const tokenRefresh = async (): Promise<string> => {
  try {
    const refresh_token = cookieUtils.get('refreshToken');
    if (window.location.hash.includes('#/invite-register')) {
      throw new Error(i18n.t('common.refreshTokenNotExist'));
    }
    if (!refresh_token) {
      throw new Error(i18n.t('common.refreshTokenNotExist'));
    }
    // Use native axios to call refresh API, avoiding interceptor circular calls
    const response: any = await refreshToken();
    const newToken = response.access_token;
    cookieUtils.set('authToken', newToken);
    return newToken;
  } catch (error) {
    // If refresh API also returns 401, logout
    clearAuthData();
    message.warning(i18n.t('common.loginExpired'));
    // Redirect to login page
    if (!window.location.hash.includes('#/login')) {
      window.location.href = `/#/login`;
    }
    throw error;
  }
};

// Response interceptor
service.interceptors.response.use(
  (response) => {
    // Process response data
    const { data: responseData } = response;

    // If response data is not an object, return directly
    if (!responseData || typeof responseData !== 'object') {
      return responseData;
    }

    const { data, code } = responseData;

    switch (code) {
      case 0:
      case 200:
        return data !== undefined ? data : responseData;
      case 401:
        // Handle unauthorized
        return handle401Error(response.config);
      default:
        if (code === undefined) {
          return responseData;
        }
        if (responseData.error || responseData.msg) {
          message.warning(responseData.error || responseData.msg)
        }
        return Promise.reject(responseData);
    }
  },
  (error) => {
    // If request was cancelled, don't show error message
    if (axios.isCancel(error) || error.name === 'AbortError' || error.code === 'ERR_CANCELED') {
      return Promise.reject(error);
    }

    // Handle network errors, timeouts, etc.
    let msg = error.response?.data?.error || error.response?.error;
    const status = error?.response ? error.response.status : error;
    // Server responded but status code is not in 2xx range
    switch (status) {
      case 401:
        // Handle unauthorized
        return handle401Error(error.config);
      case 403:
        msg = i18n.t('common.permissionDenied');
        break;
      case 404:
        msg = i18n.t('common.apiNotFound');
        break;
      case 429:
        msg = i18n.t('common.tooManyRequests');
        break;
      case 500:
      case 502:
        msg = msg || i18n.t('common.serviceUpgrading');
        break;
      case 504:
        msg = msg || i18n.t('common.serverError');
        break;
      default:
        if (!msg && Array.isArray(error.response?.data?.detail)) {
          msg = error.response?.data?.detail?.map((item: { msg: string }) => item.msg).join(';')
        } else {
          msg = msg || i18n.t('common.unknownError');
        }
        break;
    }
    message.warning(msg);
    return Promise.reject(error);
  }
);

/**
 * Handle 401 unauthorized errors with token refresh
 * @param config - Original request configuration
 * @returns Retried request with new token
 */
const handle401Error = async (config: AxiosRequestConfig): Promise<unknown> => {
  // If refresh API itself returns 401, logout directly
  if (config.url === refreshTokenUrl) {
    clearAuthData();
    message.warning(i18n.t('common.loginExpired'));
    return Promise.reject(new Error(i18n.t('common.loginExpired')));
  }
  if (config.url === loginUrl) {
    return Promise.reject(new Error(i18n.t('common.loginApiCannotRefreshToken')));
  }
  if (config.url === logoutUrl) {
    window.location.href = `/#/login`;
    return Promise.reject(new Error(i18n.t('common.logoutApiCannotRefreshToken')));
  }
  if (config.url?.includes('/public')) {
    return Promise.reject(new Error(i18n.t('common.publicApiCannotRefreshToken')));
  }

  // If token refresh is in progress, queue the request
  if (isRefreshing) {
    return new Promise((resolve, reject) => {
      requests.push({ config, resolve, reject });
    }).then((token) => {
      // Retry request with new token
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${token}`;
      return service(config);
    });
  }

  // Start token refresh
  isRefreshing = true;
  try {
    const newToken = await tokenRefresh();
    
    // Update token for all queued requests and resolve them
    requests.forEach(({ config, resolve }) => {
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${newToken}`;
      resolve(newToken);
    });
    
    // Clear queue
    requests = [];
    
    // Retry current request with new token
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${newToken}`;
    return service(config);
  } catch (error) {
    // Token refresh failed, clear queue and reject all requests
    requests.forEach(({ reject }) => {
      reject(error as Error);
    });
    requests = [];
    return Promise.reject(error);
  } finally {
    isRefreshing = false;
  }
};

interface ObjectWithPush {
  _push?: boolean;
  [key: string]: string | number | boolean | object | null | undefined;
}

/**
 * Filter and clean request parameters
 * - Removes null/undefined values
 * - Trims string values
 * - Handles objects with _push flag
 */
function paramFilter(params: Record<string, string | number | boolean | ObjectWithPush | null | undefined> = {}) {

  Object.keys(params).forEach(key => {
    const val = params[key];
    if (val && typeof(val) === 'object'){
      const objVal = val as ObjectWithPush;
      if(objVal._push){ 
        delete objVal._push;
      }else{
        delete params[key];
      }
    } else if(val || val === 0 || val === false){
      if(typeof(val) === 'string'){
        params[key] = val.trim();
      }
    }else{
      delete params[key];
    }
  });

  return params;
}

/**
 * HTTP request methods wrapper
 */
export const request = {
  get<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return service.get(url, {
      params: paramFilter(data as Record<string, string | number | boolean | ObjectWithPush | null | undefined>),
      ...config || {}
    });
  },
  
  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return service.post(url, data, config);
  },
  
  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return service.put(url, data, config);
  },
  
  delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return service.delete(url, config);
  },
  
  patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return service.patch(url, data, config);
  },
  uploadFile<T>(url: string, formData?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return service.post(url, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      withCredentials: false,
      ...config
    });
  },
  downloadFile(url: string, fileName: string, data?: unknown, callback?: () => void) {
    service.post(url, data, {
      responseType: "blob",
    })
    .then(res =>{
      const link = document.createElement("a");
      const blob = new Blob([res as unknown as BlobPart]);
      link.style.display = "none";
      link.href = URL.createObjectURL(blob);
      link.setAttribute("download", decodeURI(fileName || fileName));
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      callback?.()
    });
  }
};



/**
 * Get parent domain for cookie setting
 * @returns Parent domain or IP address
 */
const getParentDomain = () => {
  const hostname = window.location.hostname
  // Check if it's an IP address
  if (/^\d+\.\d+\.\d+\.\d+$/.test(hostname)) {
    return hostname
  }
  const parts = hostname.split('.')
  return parts.length > 2 ? `.${parts.slice(-2).join('.')}` : hostname
}

/**
 * Cookie utility functions
 */
export const cookieUtils = {
  set: (name: string, value: string, domain = getParentDomain()) => {
    document.cookie = `${name}=${value}; domain=${domain}; path=/; secure; samesite=strict`
  },
  get: (name: string) => {
    const value = `; ${document.cookie}`
    const parts = value.split(`; ${name}=`)
    return parts.length === 2 ? parts.pop()?.split(';').shift() : null
  },
  remove: (name: string, domain = getParentDomain()) => {
    document.cookie = `${name}=; domain=${domain}; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`
  },
  clear: (domain = getParentDomain()) => {
    document.cookie.split(';').forEach(cookie => {
      const eqPos = cookie.indexOf('=');
      const name = eqPos > -1 ? cookie.substr(0, eqPos).trim() : cookie.trim();
      if (name) {
        document.cookie = `${name}=; domain=${domain}; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
        document.cookie = `${name}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
      }
    });
  },
}


export default service;