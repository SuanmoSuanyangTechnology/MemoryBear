import { createRoot } from 'react-dom/client'
import '@/styles/index.css'
import App from '@/App.tsx'

// 同步导入i18n配置以确保在组件渲染前初始化完成
import './i18n'

createRoot(document.getElementById('root')!)
.render(
  <App />
)
