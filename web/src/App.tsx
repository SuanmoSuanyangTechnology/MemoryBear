/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-24 19:00:14
 * @LastEditors: yujiangping
 * @LastEditTime: 2025-11-25 18:48:26
 */
import { RouterProvider } from 'react-router-dom';
import { 
  Suspense, 
  useEffect
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

import menuJson from '@/store/menu.json';

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
    <ConfigProvider
      locale={locale}
      theme={lightTheme}
    >
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
    </ConfigProvider>
  );
}

export default App
