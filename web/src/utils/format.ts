/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:34:43 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-17 17:17:56
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
