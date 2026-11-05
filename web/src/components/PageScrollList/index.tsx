/**
 * PageScrollList Component
 * 
 * An infinite scroll list component with pagination support that:
 * - Automatically loads more data when scrolling to bottom
 * - Supports grid layout with configurable columns
 * - Handles loading and empty states
 * - Exposes refresh method via ref
 * 
 * @component
 */

import React, { useEffect, useState, useRef, useId, forwardRef, useImperativeHandle } from 'react';
import { Row, Col } from 'antd';
import InfiniteScroll from 'react-infinite-scroll-component';

import { request } from '@/utils/request';
import PageEmpty from '@/components/Empty/PageEmpty'
import PageLoading from '@/components/Empty/PageLoading'

/** Default page size for pagination */
const PAGE_SIZE = 20;

/** API response structure with pagination metadata */
interface ApiResponse<T> {
  items?: T[];
  [key: string]: unknown;
  page: {
    page: number;
    pagesize: number;
    total: number;
    hasnext: boolean;
    has_next?: boolean;
  };
}

/** Ref methods exposed to parent component */
export interface PageScrollListRef {
  refresh: () => void;
  total: number;
}

/** Props interface for PageScrollList component */
interface PageScrollListProps<T, Q = Record<string, unknown>> {
  /** API endpoint URL */
  url: string;
  /** HTTP method; GET sends query params, POST sends a JSON body. */
  method?: 'get' | 'post';
  /** Function to render each list item */
  renderItem?: (item: T, index: number) => React.ReactNode;
  /** Optional renderer that receives all currently loaded items */
  renderItems?: (items: T[]) => React.ReactNode;
  /** Response field containing list items; defaults to items. */
  itemsKey?: string;
  header?: React.ReactNode;
  /** Query parameters for API request */
  query?: Q;
  /** Number of columns in grid layout */
  column?: number;
  /** Additional CSS classes */
  className?: string;
  needLoading?: boolean;
  pageLoading?: React.ReactNode;
  heightClass?: string;
  gutter?: [number, number] | number;
  onTotalChange?: (total: number) => void;
  empty?: React.ReactNode;
  onLoad?: (res: any) => void;
  onError?: () => void;
  onLoadingChange?: (loading: boolean) => void;
}

const defaultHeightClass = 'rb:h-[calc(100vh-118px)]!';

/** Infinite scroll list component with pagination support */
const PageScrollList = forwardRef(<T, Q = Record<string, unknown>>({
  renderItem,
  renderItems,
  itemsKey = 'items',
  header,
  query,
  url,
  method = 'get',
  column = 4,
  className = '',
  pageLoading,
  needLoading = true,
  heightClass,
  gutter = [12, 12],
  onTotalChange,
  empty,
  onLoad,
  onError,
  onLoadingChange,
}: PageScrollListProps<T, Q>, ref: React.Ref<PageScrollListRef>) => {
  const scrollableTargetId = `page-scroll-list-${useId().replace(/:/g, '')}`;
  /** Expose refresh method to parent component */
  useImperativeHandle(ref, () => ({
    refresh: () => {
      pageRef.current = 1;
      loadingRef.current = false;
      hasMoreRef.current = true;
      setHasMore(true);
      setData([]);
      loadMoreData(true);
    },
    total,
  }));
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<T[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRef = useRef(1);
  const requestVersionRef = useRef(0);
  const loadingRef = useRef(false);
  const hasMoreRef = useRef(true);
  const [total, setTotal] = useState(0);
  const onLoadRef = useRef(onLoad);
  useEffect(() => {
    onLoadRef.current = onLoad;
  }, [onLoad]);

  /** Load more data from API with pagination */
  const loadMoreData = (reset?: boolean) => {
    if (loadingRef.current || (!reset && !hasMoreRef.current)) return;
    const requestVersion = ++requestVersionRef.current;
    loadingRef.current = true;
    setLoading(true);
    onLoadingChange?.(true);
    const currentPage = reset ? 1 : pageRef.current;
    request[method](url, {
      page: currentPage,
      pagesize: PAGE_SIZE,
      ...(query || {}),
    })
      .then((res) => {
        if (requestVersion !== requestVersionRef.current) return;
        const response = res as ApiResponse<T>;
        const items = response[itemsKey];
        const results = Array.isArray(items) ? items as T[] : Array.isArray(response) ? response as T[] : [];
        pageRef.current = response.page.page + 1;
        setData(prev => reset ? results : [...prev, ...results]);
        hasMoreRef.current = response.page?.has_next || response.page?.hasnext;
        setHasMore(response.page?.has_next || response.page?.hasnext);
        const newTotal = response.page?.total || 0;
        setTotal(newTotal);
        onTotalChange?.(newTotal);
        onLoadRef.current?.(res)
      })
      .catch(() => {
        if (requestVersion !== requestVersionRef.current) return;
        hasMoreRef.current = false;
        setHasMore(false);
        onError?.();
      })
      .finally(() => {
        if (requestVersion !== requestVersionRef.current) return;
        loadingRef.current = false;
        setLoading(false);
        onLoadingChange?.(false);
        // 内容不足以填满容器时，主动继续加载
        setTimeout(() => {
          if (requestVersion !== requestVersionRef.current) return;
          const el = scrollRef.current;
          if (el && hasMoreRef.current && el.scrollHeight <= el.clientHeight) {
            loadMoreData();
          }
        }, 0);
      });
  };

  /** Reset and reload when query parameters change */
  const queryKey = JSON.stringify(query);
  useEffect(() => {
    pageRef.current = 1;
    loadingRef.current = false;
    hasMoreRef.current = true;
    setHasMore(true);
    setData([]);
    loadMoreData(true);
    return () => { requestVersionRef.current += 1; };
  }, [queryKey, url, method, itemsKey]);

  return (
    <div
      ref={scrollRef}
      id={scrollableTargetId}
      className={`rb:overflow-y-auto rb:overflow-x-hidden rb:min-h-0! ${heightClass || defaultHeightClass} ${className}`}
    >
      {header}
      <InfiniteScroll
        dataLength={data.length}
        next={() => loadMoreData()}
        hasMore={hasMore}
        loader={loading && needLoading ? pageLoading || <PageLoading className={heightClass || defaultHeightClass} /> : false}
        // endMessage={<Divider plain>It is all, nothing more 🤐</Divider>}
        scrollThreshold={0.9}
        scrollableTarget={scrollableTargetId}
        style={{ overflow: 'visible', width: '100%' }}
      >
        {/* Render grid list or empty state */}
        {data.length > 0 ? (
          renderItems
          ? renderItems(data)
          : (
            <Row gutter={gutter}>
              {data.map((item, index) => (
                <Col key={(item as any).id || index} span={24/column}>
                  {renderItem?.(item, index)}
                </Col>
              ))}
            </Row>
          )
        ) : !loading ? empty || <PageEmpty className={heightClass || defaultHeightClass} /> : null}
      </InfiniteScroll>
    </div>
  );
}) as <T = Record<string, unknown>, Q = Record<string, unknown>>(props: PageScrollListProps<T, Q> & { ref?: React.Ref<PageScrollListRef> }) => React.ReactElement;

export default PageScrollList;