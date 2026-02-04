/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:18:19 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-03 15:44:42
 */
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

import React, { useEffect, useState, useRef, forwardRef, useImperativeHandle } from 'react';
import { List } from 'antd';
import InfiniteScroll from 'react-infinite-scroll-component';

import { request } from '@/utils/request';
import PageEmpty from '@/components/Empty/PageEmpty'
import PageLoading from '@/components/Empty/PageLoading'

/** Default page size for pagination */
const PAGE_SIZE = 20;

/** API response structure with pagination metadata */
interface ApiResponse<T> {
  items?: T[];
  page: {
    page: number;
    pagesize: number;
    total: number;
    hasnext: boolean;
  };
}

/** Ref methods exposed to parent component */
export interface PageScrollListRef {
  refresh: () => void;
}

/** Props interface for PageScrollList component */
interface PageScrollListProps<T, Q = Record<string, unknown>> {
  /** API endpoint URL */
  url: string;
  /** Function to render each list item */
  renderItem: (item: T) => React.ReactNode;
  /** Query parameters for API request */
  query?: Q;
  /** Number of columns in grid layout */
  column?: number;
  /** Additional CSS classes */
  className?: string;
  needLoading?: boolean;
}

/** Infinite scroll list component with pagination support */
const PageScrollList = forwardRef(<T, Q = Record<string, unknown>>({
  renderItem, 
  query, 
  url,
  column = 4,
  className = '',
  needLoading = true,
}: PageScrollListProps<T, Q>, ref: React.Ref<PageScrollListRef>) => {
  /** Expose refresh method to parent component */
  useImperativeHandle(ref, () => ({
    refresh,
  }));
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<T[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  /** Load more data from API with pagination */
  const loadMoreData = (flag?: boolean) => {
    if (!flag && (loading || !hasMore)) {
      return;
    }
    setLoading(true);
    request.get(url, {
      page: page,
      pagesize: PAGE_SIZE,
      ...(query||{}),
    })
      .then((res) => {
        const response = res as ApiResponse<T>;
        const results = Array.isArray(response.items) ? response.items : Array.isArray(response) ? response as T[] : [];
        // Replace data if flag is true, otherwise append
        if (flag) {
          setData(results);
        } else {
          setData(data.concat(results));
        }
        setPage(response.page.page + 1);
        setHasMore(response.page?.hasnext);
        setLoading(false);
        console.log(`${results.length} more items loaded!`);
      })
      .catch(() => {
        setLoading(false);
        setHasMore(false);
        console.error('Failed to load data');
      })
      .finally(() => {
        setLoading(false);
      });
  };

  /** Reset list to initial state and reload data */
  const refresh = () => {
    setPage(1);
    setHasMore(true);
    setData([]);
  }

  /** Refresh when query parameters change */
  useEffect(() => {
    refresh()
  }, [query]);

  /** Load initial data when list is reset */
  useEffect(() => {
    if (page === 1 && hasMore && data.length === 0) {
      loadMoreData(true);
    }
  }, [page, hasMore, data])
  
  return (
    <>
      <div
        ref={scrollRef}
        id="scrollableDiv"
        className={`rb:overflow-y-auto rb:overflow-x-hidden rb:h-[calc(100vh-148px)] ${className}`}
      >
        <InfiniteScroll
          dataLength={data.length}
          next={loadMoreData}
          hasMore={hasMore}
          loader={loading && needLoading ? <PageLoading /> : false}
          // endMessage={<Divider plain>It is all, nothing more 🤐</Divider>}
          scrollableTarget="scrollableDiv"
          className='rb:h-full!'
        >
          {/* Render grid list or empty state */}
          {data.length > 0 ? (
            <List
              grid={{ gutter: 16, column: column }}
              dataSource={data}
              renderItem={(item) => (
                <List.Item>
                  {renderItem(item)}
                </List.Item>
              )}
            />
          ) : !loading ? <PageEmpty /> : null}
        </InfiniteScroll>
      </div>
    </>
  );
}) as <T = Record<string, unknown>, Q = Record<string, unknown>>(props: PageScrollListProps<T, Q> & { ref?: React.Ref<PageScrollListRef> }) => React.ReactElement;

export default PageScrollList;