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
import type { AxiosRequestConfig, InternalAxiosRequestConfig } from 'axios';
import { clearAuthData } from './auth';
import { message } from 'antd';
import { refreshTokenUrl, refreshToken, loginUrl, logoutUrl } from '@/api/user'
import i18n from '@/i18n'

/**
 * Handle logout: clear auth data, show warning message, redirect to login page
 */
export const handleLogout = (msg?: string) => {
  clearAuthData();
  if (msg) {
    message.warning(msg);
  }
  if (!window.location.hash.includes('#/login')) {
    window.location.href = `/#/login`;
  }
};

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

let lastTraceId = ''
let lastRetryAfter = 0

export const getLastTraceId = () => lastTraceId
export const setLastTraceId = (id: string) => { lastTraceId = id }
export const getLastRetryAfter = () => lastRetryAfter
export const setLastRetryAfter = (seconds: number) => { lastRetryAfter = seconds }

const RATE_LIMIT_CODES = new Set([3014, 3015, 3016, 3017, 3019, 10004])

const parseRetryAfter = (value: unknown): number => {
  if (!value) return 0
  const num = typeof value === 'string' ? parseInt(value, 10) : Number(value)
  return Number.isFinite(num) && num > 0 ? num : 0
}

const DEFAULT_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30000

export const getRetryDelayMs = (attempt = 0): number => {
  if (lastRetryAfter > 0) {
    const serverDelay = lastRetryAfter * 1000
    lastRetryAfter = 0
    return serverDelay
  }
  return Math.min(DEFAULT_BACKOFF_MS * Math.pow(2, attempt), MAX_BACKOFF_MS)
}

export const isRateLimitCode = (code: number): boolean => RATE_LIMIT_CODES.has(code)

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
    if (!config.headers.Authorization && !config.url?.includes('/public') && !config.url?.includes('/storage/files')) {
      const token = cookieUtils.get('authToken');
      const refresh_token = cookieUtils.get('refreshToken');
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      } else if (refresh_token) {
        if (config.url === refreshTokenUrl || config.url === loginUrl || config.url === logoutUrl || config.url?.includes('/public')) {
          return config;
        }
        if (isRefreshing) {
          return new Promise<InternalAxiosRequestConfig>((resolve) => {
            requests.push({
              config,
              resolve: (newToken: string) => {
                config.headers = config.headers || {};
                config.headers.Authorization = `Bearer ${newToken}`;
                resolve(config);
              },
              reject: () => resolve(config),
            });
          });
        }
        isRefreshing = true;
        return tokenRefresh().then((newToken) => {
          requests.forEach(({ config: queuedConfig, resolve }) => {
            queuedConfig.headers = queuedConfig.headers || {};
            queuedConfig.headers.Authorization = `Bearer ${newToken}`;
            resolve(newToken);
          });
          requests = [];
          config.headers = config.headers || {};
          config.headers.Authorization = `Bearer ${newToken}`;
          return config;
        }).catch(() => {
          requests.forEach(({ reject }) => reject(new Error('refresh failed')));
          requests = [];
          return config;
        }).finally(() => {
          isRefreshing = false;
        });
      } else {
        if (config.url === refreshTokenUrl || config.url === loginUrl || config.url === logoutUrl || config.url?.includes('/public')) {
          return config;
        }
        handleLogout();
        return Promise.reject({ msg: i18n.t('common.loginExpired') });
      }
    }
    const language = localStorage.getItem('language')
    const timeZone = localStorage.getItem('timeZone')
    config.headers['X-Language-Type'] = language || 'en';
    config.headers['Accept-Language'] = language || 'en';
    config.headers['X-Timezone'] = timeZone || 'Asia/Shanghai'
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
    cookieUtils.set('refreshToken', response.refresh_token);
    return newToken;
  } catch (error: any) {
    if (error?.code === 2001 || error?.response?.data?.code === 2001) {
      throw error;
    }
    clearAuthData();
    message.warning(i18n.t('common.loginExpired'));
    if (!window.location.hash.includes('#/login')) {
      window.location.href = `/#/login`;
    }
    throw error;
  }
};

// Response interceptor
service.interceptors.response.use(
  (response) => {
    const traceId = response.headers?.['x-trace-id'] || response.headers?.['X-Trace-Id']
    if (traceId) lastTraceId = traceId

    const { data: responseData } = response;

    if (!responseData || typeof responseData !== 'object') {
      return responseData;
    }

    const { data, code } = responseData;

    switch (code) {
      case 0:
        return data !== undefined ? data : responseData;
      case 2001:
        if (response.config?.url?.includes('/public')) {
          if (responseData.msg) {
            message.warning(responseData.msg);
          }
          return Promise.reject(responseData);
        }
        if (response.config?.url === refreshTokenUrl) {
          return Promise.reject(responseData);
        }
        return handle401Error(response.config);
      case 2002:
      case 2003:
        return handle401Error(response.config);
      case 2004:
        clearAuthData();
        message.warning(responseData.msg || i18n.t('common.loginExpired'));
        if (!window.location.hash.includes('#/login')) {
          window.location.href = `/#/login`;
        }
        return Promise.reject(responseData);
      case 4009:
        clearAuthData();
        message.warning(responseData.msg || i18n.t('common.permissionDenied'));
        if (!window.location.hash.includes('#/login')) {
          window.location.href = `/#/login`;
        }
        return Promise.reject(responseData);
      default:
        if (code === undefined) {
          return responseData;
        }
        if (isRateLimitCode(code)) {
          const retryAfter = parseRetryAfter(response.headers?.['retry-after'] || response.headers?.['Retry-After'])
          if (retryAfter) lastRetryAfter = retryAfter
        }
        if (responseData.msg) {
          message.warning(responseData.msg)
        }
        return Promise.reject(responseData);
    }
  },
  async (error) => {
    if (axios.isCancel(error) || error.name === 'AbortError' || error.code === 'ERR_CANCELED') {
      return Promise.reject(error);
    }

    const errTraceId = error.response?.headers?.['x-trace-id'] || error.response?.headers?.['X-Trace-Id']
    if (errTraceId) lastTraceId = errTraceId

    let msg = error.response?.data?.msg || error.response?.msg || error.msg;

    if (error.response?.data instanceof Blob) {
      try {
        const text = await error.response.data.text();
        const errorData = JSON.parse(text);
        msg = errorData.msg || msg;
      } catch {
        msg = msg || i18n.t('common.unknownError');
      }
    }
    const status = error?.response ? error.response.status : error;
    const errorCode = error.response?.data?.code;
    const isPublicApi = error.config?.url?.includes('/public');
    switch (status) {
      case 401:
        if (error.config?.url === refreshTokenUrl) {
          if (errorCode === 2001) {
            message.warning(error.response?.data?.msg || i18n.t('common.serviceUpgrading'));
            return Promise.reject(error);
          }
          handleLogout(error.response?.data?.msg || i18n.t('common.loginExpired'));
          return Promise.reject(error);
        }
        if (errorCode === 2001) {
          if (isPublicApi) {
            message.warning(error.response?.data?.msg || i18n.t('common.loginRequired'));
            return Promise.reject(error);
          }
          return handle401Error(error.config);
        }
        if (errorCode === 2002) {
          return handle401Error(error.config);
        }
        if (errorCode === 2004) {
          if (isPublicApi) {
            message.warning(error.response?.data?.msg || i18n.t('common.loginExpired'));
            return Promise.reject(error);
          }
          handleLogout(error.response?.data?.msg || i18n.t('common.loginExpired'));
          return Promise.reject(error);
        }
        if (errorCode === 4009) {
          if (isPublicApi) {
            message.warning(error.response?.data?.msg || i18n.t('common.permissionDenied'));
            return Promise.reject(error);
          }
          handleLogout(error.response?.data?.msg || i18n.t('common.permissionDenied'));
          return Promise.reject(error);
        }
        if (isPublicApi) {
          message.warning(error.response?.data?.msg || i18n.t('common.loginRequired'));
          return Promise.reject(error);
        }
        return handle401Error(error.config);
      case 403:
        msg = msg || i18n.t('common.permissionDenied');
        break;
      case 404:
        msg = msg || i18n.t('common.apiNotFound');
        break;
      case 429:
      case 503: {
        msg = msg || (status === 429 ? i18n.t('common.tooManyRequests') : i18n.t('common.serviceUpgrading'));
        const retryAfter = parseRetryAfter(error.response?.headers?.['retry-after'] || error.response?.headers?.['Retry-After'])
        if (retryAfter) lastRetryAfter = retryAfter
        break;
      }
      case 500:
      case 502:
        msg = msg || i18n.t('common.serviceUpgrading');
        break;
      case 504:
        msg = msg || i18n.t('common.serviceUpgrading');
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
  } catch (error: any) {
    requests.forEach(({ reject }) => {
      reject(error as Error);
    });
    requests = [];
    if (error?.code === 2001 || error?.response?.data?.code === 2001) {
      message.warning(error?.msg || error?.response?.data?.msg || i18n.t('common.serviceUpgrading'));
    }
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

const toDataPromise = <T>(requestPromise: Promise<unknown>): Promise<T> => {
  // The response interceptor above unwraps AxiosResponse to business data.
  return requestPromise as Promise<T>;
};

/**
 * HTTP request methods wrapper
 */
export const request = {
  get<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return toDataPromise<T>(service.get(url, {
      params: paramFilter(data as Record<string, string | number | boolean | ObjectWithPush | null | undefined>),
      ...config || {}
    }));
  },
  
  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return toDataPromise<T>(service.post(url, data, config));
  },
  
  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return toDataPromise<T>(service.put(url, data, config));
  },
  
  delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return toDataPromise<T>(service.delete(url, config));
  },
  
  patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return toDataPromise<T>(service.patch(url, data, config));
  },
  uploadFile<T>(url: string, formData?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return toDataPromise<T>(service.post(url, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      withCredentials: false,
      ...config
    }));
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
  },
  getDownloadFile(url: string, fileName: string, data?: unknown, callback?: () => void) {
    service.get(url, {
      params: paramFilter(data as Record<string, string | number | boolean | ObjectWithPush | null | undefined>),
      responseType: "blob",
    })
      .then(res => {
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
const isIp = (hostname: string) => /^\d+\.\d+\.\d+\.\d+$/.test(hostname)

const getParentDomain = () => {
  const hostname = window.location.hostname
  if (isIp(hostname)) return hostname
  const parts = hostname.split('.')
  return parts.length > 2 ? `.${parts.slice(-2).join('.')}` : hostname
}

/**
 * Cookie utility functions
 */
export const cookieUtils = {
  set: (name: string, value: string, domain = getParentDomain()) => {
    const ip = isIp(window.location.hostname)
    const domainPart = ip ? '' : `; domain=${domain}`
    const securePart = window.location.protocol === 'https:' ? '; secure' : ''
    document.cookie = `${name}=${value}${domainPart}; path=/${securePart}; samesite=strict`
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