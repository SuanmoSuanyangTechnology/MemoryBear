/*
 * @Author: ZhaoYing 
 * @Date: 2026-07-14 16:12:48 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-07-14 16:12:48 
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

import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import pageEmptyIcon from '@/assets/images/empty/pageEmpty.png';
import Empty from '@/components/Empty';

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
        url={pageEmptyIcon}
        title={t('empty.loadError')}
        subTitle={t('empty.loadErrorDesc')}
        size={[240, 210]}
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

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, info);
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

export default ErrorBoundary;
