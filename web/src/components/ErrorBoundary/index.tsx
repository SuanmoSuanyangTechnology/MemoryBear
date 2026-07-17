/*
 * @Author: ZhaoYing 
 * @Date: 2026-07-14 16:12:48 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-14 16:20:33
 */
/**
 * ErrorBoundary Component
 *
 * A route-level error boundary that wraps only the content area (Outlet).
 * When a lazy-loaded route chunk fails to load (e.g. old assets removed after
 * a new deployment, triggering `vite:preloadError`), the boundary catches the
 * error and renders an in-place fallback with a reload action — while the
 * sidebar menu and header stay mounted and visible.
 *
 * @component
 */

import { Component, useEffect, type ErrorInfo, type ReactNode } from 'react';
import { useLocation, useRouteError } from 'react-router-dom';
import { Button, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import loadErrorIcon from '@/assets/images/empty/loadError.png';
import Empty from '@/components/Empty';
import { useStaticAssetError } from '@/hooks/useStaticAssetError';

/** Detect dynamic import / chunk preload failures across browsers. */
export function isChunkLoadError(error: unknown): boolean {
  if (!error) return false;
  const message = error instanceof Error ? error.message : String(error);
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|dynamically imported module|ChunkLoadError|Loading chunk/i.test(
    message,
  );
}

/** Functional fallback so we can use hooks (i18n) outside the class component. */
const ErrorFallback = ({ onReload }: { onReload: () => void }) => {
  const { t } = useTranslation();
  return (
    <Flex align="center" justify="center" vertical className="rb:h-full!">
      <Empty
        url={loadErrorIcon}
        title={t('empty.loadError')}
        subTitle={t('empty.loadErrorDesc')}
        size={[300, 200]}
      />
      <Button type="primary" className="rb:mt-4" onClick={onReload}>
        {t('empty.reload')}
      </Button>
    </Flex>
  );
};

interface ErrorBoundaryProps {
  children: ReactNode;
  /** When this value changes (e.g. route pathname), a caught error is cleared. */
  resetKey?: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, info);
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps) {
    // On route change, drop the error state so a normally-loading page renders.
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return <ErrorFallback onReload={this.handleReload} />;
    }
    return this.props.children;
  }
}

/**
 * Wrapper that injects the current route path as the reset key, so switching to
 * a route whose resources load fine clears any previously caught error. It also
 * watches for failed bundled static assets (logo, menu icons, etc.) — which the
 * class boundary can't catch, since their `error` events never reach React — and
 * renders the same fallback when one fails to load.
 */
const RouteErrorBoundary = ({ children }: { children: ReactNode }) => {
  const { pathname } = useLocation();
  const hasAssetError = useStaticAssetError(pathname);

  if (hasAssetError) {
    return <ErrorFallback onReload={() => window.location.reload()} />;
  }

  return <ErrorBoundary resetKey={pathname}>{children}</ErrorBoundary>;
};

/**
 * Error element for React Router data routers (`createHashRouter`).
 *
 * When a route `element` fails to render — most commonly a lazy-loaded layout
 * or page chunk that can't be fetched after a redeploy — the data router
 * intercepts the error itself; it never reaches a React error boundary, so
 * without this it falls through to the router's default dev error screen. Wire
 * this as the route `errorElement` to show our own fallback instead.
 */
export const RouteErrorElement = () => {
  const error = useRouteError();

  useEffect(() => {
    console.error('Route error caught by errorElement:', error);
  }, [error]);

  return <ErrorFallback onReload={() => window.location.reload()} />;
};

export default RouteErrorBoundary;
