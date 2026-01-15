/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-01-05 17:22:23
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-01-15 21:02:43
 */
import { create } from 'zustand'
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import type { Locale } from 'antd/es/locale';
import dayjs from 'dayjs'
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';
import i18n from '@/i18n';
import { timezoneToAntdLocaleMap } from '@/utils/timezones';

// 扩展dayjs插件
dayjs.extend(utc);
dayjs.extend(timezone);

// 自定义中文 locale，修改 Tour 组件的按钮文字
const customZhCN: Locale = {
  ...zhCN,
  Tour: {
    ...zhCN.Tour,
    Next: '下一步',
    Previous: '上一步',
    Finish: '立即体验',
  },
};

// 自定义英文 locale，修改 Tour 组件的按钮文字
const customEnUS: Locale = {
  ...enUS,
  Tour: {
    ...enUS.Tour,
    Next: 'Next',
    Previous: 'Previous',
    Finish: 'Try it now',
  },
};


interface I18nState {
  language: string;
  locale: Locale;
  timeZone: string;
  changeLanguage: (language: string) => void;
  changeTimeZone: (timeZone: string) => void;
}

const initialTimeZone = localStorage.getItem('timeZone') || 'Asia/Shanghai'
const initialLanguage = localStorage.getItem('language') || 'en'
const initialLocale = initialLanguage === 'en' ? customEnUS : customZhCN
i18n.changeLanguage(initialLanguage)

export const useI18n = create<I18nState>((set, get) => ({
  language: initialLanguage,
  locale: initialLocale,
  timeZone: initialTimeZone,
  changeLanguage: (language: string) => {
    i18n.changeLanguage(language)
    const localeName = language === 'en' ? customEnUS : customZhCN;
    set({ language: language, locale: localeName })
  },
  changeTimeZone: (timeZone: string) => {
    const { timeZone: lastTimeZone } = get()
    set({ timeZone })
    if (lastTimeZone !== timeZone) {
      window.location.reload()
    }
  },
}))