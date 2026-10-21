import { useEffect, useState } from 'react';

/**
 * Only treat failed JavaScript and stylesheet resources from the Vite build
 * output as fatal. Images can degrade independently and should not replace an
 * otherwise usable page with a full content-area fallback.
 */
export function isCriticalAssetError(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLScriptElement) && !(target instanceof HTMLLinkElement)) {
    return false;
  }

  const url = target instanceof HTMLScriptElement ? target.src : target.href;
  if (!url) return false;

  try {
    const { origin, pathname } = new URL(url, window.location.href);
    if (origin !== window.location.origin || !/\/assets\//.test(pathname)) {
      return false;
    }

    if (target instanceof HTMLScriptElement) return true;

    const rel = target.rel.toLowerCase();
    return rel === 'stylesheet' || rel === 'modulepreload' || target.as === 'script';
  } catch {
    return false;
  }
}

/**
 * Watch for failed critical build assets in the capture phase because native
 * resource error events do not bubble into a React error boundary.
 */
export function useStaticAssetError(resetKey?: string): boolean {
  const [hasAssetError, setHasAssetError] = useState(false);

  useEffect(() => {
    const handleResourceError = (event: Event) => {
      if (isCriticalAssetError(event.target)) {
        console.error('[StaticAssetError] Critical asset failed to load.', event.target);
        setHasAssetError(true);
      }
    };

    window.addEventListener('error', handleResourceError, true);
    return () => window.removeEventListener('error', handleResourceError, true);
  }, []);

  useEffect(() => {
    setHasAssetError(false);
  }, [resetKey]);

  return hasAssetError;
}
