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
import { useTranslation } from 'react-i18next';

import { lightTheme } from './styles/antdThemeConfig.ts'
import router from './routes';
import { useI18n } from '@/store/locale'
import LayoutBg from '@/components/Layout/LayoutBg'
import dayjs from 'dayjs'
import 'dayjs/locale/en'
import 'dayjs/locale/zh-cn'
import 'dayjs/plugin/timezone'
import 'dayjs/plugin/utc'
import { cookieUtils } from './utils/request';





function App() {
  const { t } = useTranslation();
  const { locale, language, timeZone } = useI18n()
  useEffect(() => {
    const authToken = cookieUtils.get('authToken')
    if (!authToken && !window.location.hash.includes('#/login')) {
      window.location.href = `/#/login`;
    }
  }, [])

  useEffect(() => {
    document.title = t('memoryBear')
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
        <LayoutBg />
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
