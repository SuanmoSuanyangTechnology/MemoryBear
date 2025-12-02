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


interface I18nState {
  language: string;
  locale: Locale;
  timeZone: string;
  changeLanguage: (language: string) => void;
  changeTimeZone: (timeZone: string) => void;
}

const initialTimeZone = localStorage.getItem('timeZone') || 'Asia/Shanghai'
const initialLanguage = localStorage.getItem('language') || 'en'
const initialLocale = initialLanguage === 'en' ? enUS : zhCN
i18n.changeLanguage(initialLanguage)

export const useI18n = create<I18nState>((set, get) => ({
  language: initialLanguage,
  locale: initialLocale,
  timeZone: initialTimeZone,
  changeLanguage: (language: string) => {
    i18n.changeLanguage(language)
    const localeName = timezoneToAntdLocaleMap[language] || enUS;
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