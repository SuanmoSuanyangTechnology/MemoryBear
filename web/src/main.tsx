/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-02 20:28:01
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-17 14:19:14
 */
import { createRoot } from 'react-dom/client'
import '@/styles/index.css'
import App from '@/App.tsx'

// Synchronously import i18n config to ensure initialization before component rendering
import './i18n'

// Fix autofill background color on focus
document.addEventListener('animationstart', (e) => {
  if (e.animationName === 'onAutoFillStart') {
    const input = e.target as HTMLInputElement
    input.style.backgroundColor = 'transparent'
    input.addEventListener('focus', () => { input.style.backgroundColor = 'transparent' }, { once: false })
  }
})

// After a new release, old dynamic chunk files are deleted; force a page reload on preload error
window.addEventListener('vite:preloadError', () => {
  console.warn('New version detected, reloading page to load latest assets...')
  window.location.reload()
})

createRoot(document.getElementById('root')!)
.render(
  <App />
)
