import React, { useEffect, useState, useRef, forwardRef, useImperativeHandle } from 'react';
import { List, Skeleton} from 'antd';
import InfiniteScroll from 'react-infinite-scroll-component';
import { request } from '@/utils/request';
import Empty from '@/components/Empty';

const PAGE_SIZE = 20;

interface ApiResponse {
  items?: Record<string, unknown>[];
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

interface PageScrollListProps {
  url: string;
  renderItem: (item: Record<string, unknown>) => React.ReactNode;
  query?: Record<string, unknown>;
  column?: number;
  className?: string;
}

const PageScrollList = forwardRef<PageScrollListRef, PageScrollListProps>(({
  renderItem, 
  query, 
  url,
  column = 4,
  className = '',
}, ref) => {
  useImperativeHandle(ref, () => ({
    refresh,
  }));
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<Record<string, unknown>[]>([]);
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
        const response = res as ApiResponse;
        const results = Array.isArray(response.items) ? response.items : Array.isArray(response) ? response : [];
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
          loader={<Skeleton active />}
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
          ) : !loading ? <Empty /> : null}
        </InfiniteScroll>
      </div>
    </>
  );
});

export default PageScrollList;