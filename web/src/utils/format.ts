/**
 * 格式化日期时间
 * @param value 时间戳（毫秒）或日期字符串
 * @param format 目标格式，支持 YYYY-MM-DD HH:mm:ss、YYYY/MM/DD HH:mm:ss、HH:mm 等
 * @returns 格式化后的日期时间字符串
 */
import dayjs from 'dayjs';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';

// 扩展dayjs插件
dayjs.extend(utc);
dayjs.extend(timezone);
export const formatDateTime = (
  value: string | number | null | undefined,
  format: string = 'YYYY-MM-DD HH:mm:ss'
): string => {
  if (!value) return '';

  // 检查日期是否有效
  if (!dayjs(value).isValid()) {
    return '';
  }

  // 每次调用都获取最新的时区设置
  const currentTimeZone = localStorage.getItem('timeZone') || 'Asia/Shanghai';
  dayjs.tz.setDefault(currentTimeZone);
  
  // 使用最新时区格式化日期
  return dayjs.tz(value).format(format);
};

