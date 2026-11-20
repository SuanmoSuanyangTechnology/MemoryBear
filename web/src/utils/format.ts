/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:34:43 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-11-18 20:28:52
 */
/**
 * Format Utility
 * 
 * Provides date/time formatting functions with timezone support.
 * 
 * @module format
 */

import dayjs from 'dayjs';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';

/** Extend dayjs with timezone plugins */
dayjs.extend(utc);
dayjs.extend(timezone);

/**
 * Format date/time with timezone support
 * @param value - Timestamp (milliseconds) or date string
 * @param format - Target format, supports YYYY-MM-DD HH:mm:ss, YYYY/MM/DD HH:mm:ss, HH:mm, etc.
 * @returns Formatted date/time string
 */
export const formatDateTime = (
  value: string | number | null | undefined,
  format: string = 'YYYY-MM-DD HH:mm:ss',
  locale?: 'en' | 'zh'
): string => {
  if (!value) return '';

  /** Check if date is valid */
  if (!dayjs(value).isValid()) {
    return '';
  }

  /** Get current timezone setting from localStorage */
  const currentTimeZone = localStorage.getItem('timeZone') || 'Asia/Shanghai';

  if (locale) {
    return dayjs(value).tz(currentTimeZone).locale(locale).format(format);
  }
  
  return dayjs(value).tz(currentTimeZone).format(format);
};

export const formatVersion = (version?: string) => version?.startsWith('v') ? version : version ? `v${version}` : '-'

/**
 * Format a byte count into a human-readable file size
 * @param size - Byte count (or a value convertible to one)
 * @returns Formatted size, e.g. `2.4 MB`
 */
export const formatFileSize = (size: unknown): string => {
  const bytes = Number(size);
  if (!Number.isFinite(bytes) || bytes < 0) return String(size ?? '');
  if (bytes === 0) return '0 B';

  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const unitIndex = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** unitIndex;

  return unitIndex === 0 ? `${value} B` : `${value.toFixed(1)} ${units[unitIndex]}`;
};
