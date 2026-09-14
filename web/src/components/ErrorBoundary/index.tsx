/*
 * @Author: ZhaoYing
 * @Date: 2026-07-14 16:12:48
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-14 16:20:33
 */
/**
 * Route-level error handling for rendering errors and critical Vite assets.
 * The boundary is mounted around Outlet so the surrounding layout remains
 * available when a lazy route cannot be loaded after a deployment.
 */

import { Component, useEffect, type ErrorInfo, type ReactNode } from 'react';
import { useLocation, useRouteError } from 'react-router-dom';
import { Button, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import loadErrorIcon from '@/assets/images/empty/loadError.png';
import Empty from '@/components/Empty';
import { useStaticAssetError } from '@/hooks/useStaticAssetError';

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
    console.error('[RouteErrorBoundary] React render failed.', error, info);
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
 * Resets render and critical-asset errors after navigation. The key remounts
 * the class boundary instead of calling setState from componentDidUpdate.
 */
const RouteContentErrorBoundary = ({ children }: { children: ReactNode }) => {
  const { pathname, search } = useLocation();
  const resetKey = `${pathname}${search}`;
  const hasAssetError = useStaticAssetError(resetKey);

  if (hasAssetError) {
    return <ErrorFallback onReload={() => window.location.reload()} />;
  }

  return <ErrorBoundary key={resetKey}>{children}</ErrorBoundary>;
};

/**
 * Handles errors intercepted by React Router before a layout-level React error
 * boundary can receive them, including rejected lazy route imports.
 */
export const RouteErrorElement = () => {
  const error = useRouteError();

  useEffect(() => {
    console.error('[RouteErrorElement] Router failed to render the route.', error);
  }, [error]);

  return <ErrorFallback onReload={() => window.location.reload()} />;
};

export default RouteContentErrorBoundary;
