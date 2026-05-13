/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-24 19:00:14
 * @LastEditors: zhaoying zhaoyingyz@126.com
 * @LastEditTime: 2026-05-13 16:50:52
 */
import { RouterProvider } from 'react-router-dom';
import { 
  Suspense, 
  useEffect,
  type FC,
  type ReactNode,
} from 'react';
import { 
  Spin, 
  ConfigProvider,
  App as AntdApp
} from 'antd';
import i18n from 'i18next';
import { lightTheme } from './styles/antdThemeConfig.ts'
import router from './routes';
import { useI18n } from '@/store/locale'
import dayjs from 'dayjs'
import 'dayjs/locale/en'
import 'dayjs/locale/zh-cn'
import 'dayjs/plugin/timezone'
import 'dayjs/plugin/utc'
import { cookieUtils } from './utils/request';
import { useUser } from '@/store/user';
import { Provider as PrivateProvider } from '@redbear/memory-brick'

import menuJson from '@/store/menu.json';

console.log('PrivateProvider', PrivateProvider)
const isSaas = import.meta.env.VITE_PROD_ENV === 'saas'


type MenuEntry = { path: string; i18nKey: string };

function flattenMenuEntries(list: any[]): MenuEntry[] {
  const result: MenuEntry[] = [];
  for (const item of list) {
    if (item.path && item.i18nKey && item.type !== 'group') result.push({ path: item.path, i18nKey: item.i18nKey });
    if (item.subs?.length) result.push(...flattenMenuEntries(item.subs));
  }
  return result;
}

const menuEntries: MenuEntry[] = flattenMenuEntries([...menuJson.manage, ...menuJson.space]);

function pathMatches(pattern: string, path: string): boolean {
  if (pattern === path) return true;
  if (pattern.includes(':')) {
    return new RegExp('^' + pattern.replace(/:[\w-]+/g, '[^/]+') + '$').test(path);
  }
  return false;
}

function getPageTitle(pathname: string): string {
  const appName = i18n.t('memoryBear');
  const entry = menuEntries.find(e => pathMatches(e.path, pathname));
  if (!entry) return appName;
  return `${i18n.t(entry.i18nKey)} - ${appName}`;
}

const SKIP_TITLE_PATTERNS = [
  '/user-memory/detail/:id/:type',
  '/forgetting-engine/:id',
  '/memory-extraction-engine/:id',
  '/emotion-engine/:id',
  '/reflection-engine/:id',
];




// 根据环境选择 Provider：saas 用私有组件库的 Provider，其他用 antd ConfigProvider
const AppProvider: FC<{ locale: any; theme: any; children: ReactNode }> = ({ locale, theme, children }) => {
  if (isSaas && PrivateProvider) {
    return (
      <Suspense fallback={<Spin fullscreen />}>
        <PrivateProvider locale={locale} theme={theme}>
          {children}
        </PrivateProvider>
      </Suspense>
    )
  }
  return (
    <ConfigProvider locale={locale} theme={theme}>
      {children}
    </ConfigProvider>
  )
}

function App() {
  const { locale, language, timeZone } = useI18n()
  const { checkJump } = useUser();
  useEffect(() => {
    const unsubscribe = router.subscribe(({ location }) => {
      if (SKIP_TITLE_PATTERNS.some(p => pathMatches(p, location.pathname))) return;
      document.title = getPageTitle(location.pathname);
    });
    return () => unsubscribe();
  }, [])

  useEffect(() => {
    const authToken = cookieUtils.get('authToken')
    if (!authToken && !window.location.hash.includes('#/login') && !window.location.hash.includes('#/conversation/') && !window.location.hash.includes('#/jump') && !window.location.hash.includes('#/invite-register')) {
      window.location.href = `/#/login`;
    } else {
      checkJump()
    }
  }, [])

  useEffect(() => {
    if (!SKIP_TITLE_PATTERNS.some(p => pathMatches(p, router.state.location.pathname))) {
      document.title = getPageTitle(router.state.location.pathname)
    }
    dayjs.locale(language)
    localStorage.setItem('language', language)
  }, [language])
  useEffect(() => {
    // 设置dayjs的时区
    dayjs.tz.setDefault(timeZone)
    localStorage.setItem('timeZone', timeZone)
  }, [timeZone])

  return (
    <AppProvider locale={locale} theme={lightTheme}>
      <AntdApp>
        <Suspense fallback={<Spin fullscreen></Spin>}>
          <RouterProvider 
            router={router}
            future={{
              v7_startTransition: true,
            }}
          />
        </Suspense>
      </AntdApp>
    </AppProvider>
  );
}

export default App
