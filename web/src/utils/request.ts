import axios from 'axios';
import type { AxiosRequestConfig } from 'axios';
import { clearAuthData } from './auth';
import { message } from 'antd';
import { refreshTokenUrl, refreshToken, loginUrl, logoutUrl } from '@/api/user'
import i18n from '@/i18n'

export interface ResponseData {
  code: number;
  msg: string;
  data: data | Record<string, string | number | boolean | object | null | undefined>[] | object | any[];
  error: string;
  time: number;
}
interface data {
  "items": Record<string, string | number | boolean | object | null | undefined>[];
  "page": {
    "page": number;
    "pagesize": number;
    "total": number;
    "hasnext": boolean;
  }
}


// 创建axios实例
const service = axios.create({
  baseURL: '/api', // 与vite.config.ts中的代理配置对应
  // timeout: 10000, // 请求超时时间
  withCredentials: false,
  headers: {
    'Content-Type': 'application/json'
  },
});

// 是否正在刷新token
let isRefreshing = false;
// 存储待重试的请求队列
interface RequestQueueItem {
  config: AxiosRequestConfig;
  resolve: (token: string) => void;
  reject: (error: Error) => void;
}
let requests: RequestQueueItem[] = [];

// 请求拦截器
service.interceptors.request.use(
  (config) => {
    if (!config.headers.Authorization) {
      const token = cookieUtils.get('authToken');
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    }
    config.headers.Cookie = undefined
    return config;
  },
  (error) => {
    // 对请求错误做些什么
    console.error('请求错误:', error);
    return Promise.reject(error);
  }
);

// 刷新token的函数
const tokenRefresh = async (): Promise<string> => {
  try {
    const refresh_token = cookieUtils.get('refreshToken');
    if (window.location.hash.includes('#/invite-register')) {
      throw new Error(i18n.t('common.refreshTokenNotExist'));
    }
    if (!refresh_token) {
      throw new Error(i18n.t('common.refreshTokenNotExist'));
    }
    // 使用原生axios调用refresh接口，避免触发拦截器导致的循环调用
    const response: any = await refreshToken();
    const newToken = response.access_token;
    cookieUtils.set('authToken', newToken);
    return newToken;
  } catch (error) {
    // 如果refresh接口也返回401，则退出登录
    clearAuthData();
    message.warning(i18n.t('common.loginExpired'));
    // 这里可以添加重定向到登录页的逻辑
    if (!window.location.hash.includes('#/login')) {
      window.location.href = `/#/login`;
    }
    throw error;
  }
};

// 响应拦截器
service.interceptors.response.use(
  (response) => {
    // 对响应数据做点什么
    const { data: responseData } = response;

    // 如果响应数据不是对象，直接返回
    if (!responseData || typeof responseData !== 'object') {
      return responseData;
    }

    const { data, code } = responseData;

    switch (code) {
      case 0:
      case 200:
        return data !== undefined ? data : responseData;
      case 401:
        // 处理未授权情况
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
    // 处理网络错误、超时等
    let msg = error.response?.data?.error || error.response?.error;
    const status = error?.response ? error.response.status : error;
    // 服务器响应了但状态码不在2xx范围
    switch (status) {
      case 401:
        // 处理未授权情况
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
          msg = error.response?.data?.detail?.map(item => item.msg).join(';')
        } else {
          msg = msg || i18n.t('common.unknownError');
        }
        break;
    }
    message.warning(msg);
    return Promise.reject(error);
  }
);

// 处理401错误的函数
const handle401Error = async (config: AxiosRequestConfig): Promise<unknown> => {
  // 如果是refresh接口本身返回401，则直接退出登录
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

  // 如果正在刷新token，则将当前请求加入队列
  if (isRefreshing) {
    return new Promise((resolve, reject) => {
      requests.push({ config, resolve, reject });
    }).then((token) => {
      // 使用新token重新发送请求
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${token}`;
      return service(config);
    });
  }

  // 开始刷新token
  isRefreshing = true;
  try {
    const newToken = await tokenRefresh();
    
    // 更新队列中所有请求的token并重新发送
    requests.forEach(({ config, resolve }) => {
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${newToken}`;
      resolve(newToken);
    });
    
    // 清空队列
    requests = [];
    
    // 使用新token重新发送当前请求
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${newToken}`;
    return service(config);
  } catch (error) {
    // 刷新token失败，清空队列并拒绝所有请求
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

// 封装请求方法
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
  downloadFile(url: string, fileName: string, data?: unknown) {
    service.post(url, data, {
      responseType: "blob",
    })
    .then(res =>{
      const link = document.createElement("a");
      const blob = new Blob([res.data], { type: "application/vnd.ms-excel" });
      link.style.display = "none";
      link.href = URL.createObjectURL(blob);
      link.setAttribute("download", decodeURI(res.headers['filename'] || fileName));
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    });
  }
};



// 获取父级域名
const getParentDomain = () => {
  const hostname = window.location.hostname
  // 检查是否为IP地址
  if (/^\d+\.\d+\.\d+\.\d+$/.test(hostname)) {
    return hostname
  }
  const parts = hostname.split('.')
  return parts.length > 2 ? `.${parts.slice(-2).join('.')}` : hostname
}

// Cookie操作工具
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