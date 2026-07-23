/*
 * @Description: DocumentPreview 纯工具函数与常量
 */
import { cookieUtils } from '@/utils/request';

// 支持预览的文件类型
export const previewableTypes = [
  '.pdf', '.txt', '.md', '.csv',
  '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp',
  '.doc', '.docx', '.xls', '.xlsx',
  '.ppt', '.pptx',
];

// CSV 预览大小限制：1MB
export const CSV_PREVIEW_SIZE = 1 * 1024 * 1024;
// 最大预览行数
export const MAX_PREVIEW_ROWS = 500;

const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'];

// 图片扩展名到 MIME 类型的映射
export const IMAGE_MIME_MAP: Record<string, string> = {
  jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
  gif: 'image/gif', bmp: 'image/bmp', webp: 'image/webp', svg: 'image/svg+xml',
};

// 根据 fileExt / fileName / fileUrl 解析出规范化的小写扩展名（带点）
export const getFileExtension = (fileExt?: string, fileName?: string, fileUrl?: string): string => {
  if (fileExt) {
    return fileExt.toLowerCase().startsWith('.') ? fileExt.toLowerCase() : `.${fileExt.toLowerCase()}`;
  }
  const name = fileName || fileUrl || '';
  const match = name.match(/\.([^.]+)$/);
  return match ? `.${match[1].toLowerCase()}` : '';
};

export const isTextExt = (ext: string) => ext === '.txt';
export const isMarkdownExt = (ext: string) => ext === '.md';
export const isImageExt = (ext: string) => IMAGE_EXTS.includes(ext);
export const isPdfExt = (ext: string) => ext === '.pdf';
export const isWordExt = (ext: string) => ['.doc', '.docx'].includes(ext);
export const isExcelExt = (ext: string) => ['.xls', '.xlsx', '.csv'].includes(ext);
export const isCsvExt = (ext: string) => ext === '.csv';
export const isPptExt = (ext: string) => ['.ppt', '.pptx'].includes(ext);
export const isPreviewableExt = (ext: string) => previewableTypes.includes(ext);

export const getRequestUrl = (url: string): string => {
  if (url.includes('devapi.mem.redbearai.com')) {
    const parsed = new URL(url);
    return parsed.pathname;
  }
  return url;
};

export const fetchFileBuffer = async (url: string): Promise<ArrayBuffer> => {
  const requestUrl = getRequestUrl(url);
  const response = await fetch(requestUrl, {
    credentials: 'include',
    headers: {
      'Authorization': `Bearer ${cookieUtils.get('authToken') || ''}`,
    },
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
  return response.arrayBuffer();
};

export const fetchFileBufferWithLimit = async (url: string, maxBytes?: number): Promise<ArrayBuffer> => {
  const requestUrl = getRequestUrl(url);
  const headers: Record<string, string> = {
    'Authorization': `Bearer ${cookieUtils.get('authToken') || ''}`,
  };
  if (maxBytes) {
    headers['Range'] = `bytes=0-${maxBytes - 1}`;
  }
  const response = await fetch(requestUrl, {
    credentials: 'include',
    headers,
  });
  if (!response.ok && response.status !== 206) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
  return response.arrayBuffer();
};
