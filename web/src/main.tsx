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

// After a new release, old dynamic chunk files are deleted, triggering a preload
// error. Do NOT force a full-page reload here (it would wipe the sidebar menu).
// Instead, let the error propagate to the route-level ErrorBoundary, which shows
// an in-place fallback in the content area while keeping the menu visible.
window.addEventListener('vite:preloadError', (event) => {
  console.warn('Asset preload failed (possibly a new version was deployed).', event)
})

createRoot(document.getElementById('root')!)
.render(
  <App />
)
