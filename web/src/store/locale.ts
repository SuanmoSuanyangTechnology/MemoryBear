/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:33:22 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:33:22 
 */
/**
 * Locale Store
 * 
 * Manages internationalization (i18n) and localization with:
 * - Language switching (English/Chinese)
 * - Timezone management
 * - Ant Design locale configuration
 * - Custom Tour component translations
 * - Day.js timezone support
 * 
 * @store
 */

import { create } from 'zustand'
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import type { Locale } from 'antd/es/locale';
import dayjs from 'dayjs'
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';
import i18n from '@/i18n';

/** Extend dayjs with timezone plugins */
dayjs.extend(utc);
dayjs.extend(timezone);

/** Custom Chinese locale with modified Tour component button text */
const customZhCN: Locale = {
  ...zhCN,
  Tour: {
    ...zhCN.Tour,
    Next: '下一步',
    Previous: '上一步',
    Finish: '立即体验',
  },
};

/** Custom English locale with modified Tour component button text */
const customEnUS: Locale = {
  ...enUS,
  Tour: {
    ...enUS.Tour,
    Next: 'Next',
    Previous: 'Previous',
    Finish: 'Try it now',
  },
};


/** Internationalization state interface */
interface I18nState {
  /** Current language code */
  language: string;
  /** Ant Design locale object */
  locale: Locale;
  /** Current timezone */
  timeZone: string;
  /** Change application language */
  changeLanguage: (language: string) => void;
  /** Change timezone (triggers page reload) */
  changeTimeZone: (timeZone: string) => void;
}

/** Initialize from localStorage or use defaults */
const initialTimeZone = localStorage.getItem('timeZone') || 'Asia/Shanghai'
const initialLanguage = localStorage.getItem('language') || 'en'
const initialLocale = initialLanguage === 'en' ? customEnUS : customZhCN
i18n.changeLanguage(initialLanguage)

/** Internationalization store */
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
    /** Reload page if timezone changed */
    if (lastTimeZone !== timeZone) {
      window.location.reload()
    }
  },
}))
