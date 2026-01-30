import React, { useEffect, useState, useRef, forwardRef, useImperativeHandle } from 'react';
import { List } from 'antd';
import InfiniteScroll from 'react-infinite-scroll-component';
import { request } from '@/utils/request';
import PageEmpty from '@/components/Empty/PageEmpty'
import PageLoading from '@/components/Empty/PageLoading'

const PAGE_SIZE = 20;

interface ApiResponse<T> {
  items?: T[];
  page: {
    page: number;
    pagesize: number;
    total: number;
    hasnext: boolean;
  };
}
export interface PageScrollListRef {
  refresh: () => void;
}

interface PageScrollListProps<T, Q = Record<string, unknown>> {
  url: string;
  renderItem: (item: T) => React.ReactNode;
  query?: Q;
  column?: number;
  className?: string;
}
const PageScrollList = forwardRef(<T, Q = Record<string, unknown>>({
  renderItem, 
  query, 
  url,
  column = 4,
  className = '',
}: PageScrollListProps<T, Q>, ref: React.Ref<PageScrollListRef>) => {
  useImperativeHandle(ref, () => ({
    refresh,
  }));
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<T[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

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

  // 刷新列表数据
  const refresh = () => {
    setPage(1);
    setHasMore(true);
    setData([]);
  }

  useEffect(() => {
    refresh()
  }, [query]);

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
          loader={<PageLoading />}
          // endMessage={<Divider plain>It is all, nothing more 🤐</Divider>}
          scrollableTarget="scrollableDiv"
        >
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