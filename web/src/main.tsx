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

const vitePreloadReloadStorageKey = 'vite-preload-reload-entry'

function getCurrentModuleEntry() {
  return document.querySelector<HTMLScriptElement>('script[type="module"][src]')?.src ?? window.location.href
}

// A newly deployed build can remove chunks referenced by an already-open page.
// Refresh once per entry module to load the current index.html, then let the
// existing route error boundary handle a persistent failure without a reload loop.
window.addEventListener('vite:preloadError', (event) => {
  try {
    const currentEntry = getCurrentModuleEntry()

    if (sessionStorage.getItem(vitePreloadReloadStorageKey) === currentEntry) {
      return
    }

    sessionStorage.setItem(vitePreloadReloadStorageKey, currentEntry)
    event.preventDefault()
    window.location.reload()
  } catch {
    // Keep Vite's default error flow when session storage is unavailable.
  }
})

createRoot(document.getElementById('root')!)
.render(
  <App />
)
