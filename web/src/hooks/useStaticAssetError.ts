import { useEffect, useState } from 'react';

/**
 * Decide whether a failed resource is one of our bundled static assets (logo,
 * menu icons, images, css/js chunks). Only same-origin files under the build
 * `assets` dir are treated as fatal, so a broken remote/user avatar won't blow
 * the whole shell into the fallback. Mirrors the chunk-load rationale: hashed
 * files removed after a redeploy can no longer be fetched.
 */
export function isStaticAssetError(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag !== 'IMG' && tag !== 'SCRIPT' && tag !== 'LINK') return false;

  const url =
    (target as HTMLImageElement).src || (target as HTMLLinkElement).href || '';
  if (!url) return false;

  try {
    const { origin, pathname } = new URL(url, window.location.href);
    // Same-origin build output: `/assets/...` (prod) or `/src/assets/...` (dev).
    return origin === window.location.origin && /\/assets\//.test(pathname);
  } catch {
    return false;
  }
}

/**
 * Watch for failed bundled static assets and report whether one has occurred.
 *
 * `error` events from <img>/<link>/<script> don't bubble and never reach
 * React's error boundary, so we listen in the capture phase on `window`.
 *
 * @param resetKey When this value changes (e.g. route pathname), the caught
 *   error is cleared so a route whose assets load fine renders normally.
 * @returns `true` once a same-origin build asset has failed to load.
 */
export function useStaticAssetError(resetKey?: string): boolean {
  const [hasAssetError, setHasAssetError] = useState(false);

  useEffect(() => {
    const handleResourceError = (event: Event) => {
      if (isStaticAssetError(event.target)) {
        console.error('Static asset load failure:', event.target);
        setHasAssetError(true);
      }
    };
    window.addEventListener('error', handleResourceError, true);
    return () => window.removeEventListener('error', handleResourceError, true);
  }, []);

  // On route change, drop the error so a normally-loading page renders.
  useEffect(() => {
    setHasAssetError(false);
  }, [resetKey]);

  return hasAssetError;
}
