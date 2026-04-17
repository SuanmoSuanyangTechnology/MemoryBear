import { useRef, useState, useCallback, useEffect, type FC } from 'react';
import { Select, Spin, Avatar } from 'antd';
import type { SelectProps, DefaultOptionType } from 'antd/es/select';

import { request } from '@/utils/request';

interface OptionType {
  [key: string]: any;
}

interface ApiResponse<T> {
  items?: T[];
  page: { hasnext: boolean };
}

export interface DebounceSelectProps extends Omit<SelectProps, 'options'> {
  /** API endpoint URL — mutually exclusive with fetchOptions */
  url?: string;
  /** Extra query params merged with the search keyword */
  params?: Record<string, unknown>;
  /** Key used as option value */
  valueKey?: string;
  /** Key used as option label */
  labelKey?: string;
  /** Key name sent to the API for the search keyword */
  searchKey?: string;
  pageSize?: number;
  /** Custom fetch function — mutually exclusive with url */
  fetchOptions?: (search: string | null, page: number) => Promise<{ options: DefaultOptionType[]; hasMore: boolean }>;
  /** Transform raw API items before rendering */
  format?: (items: OptionType[]) => OptionType[];
  debounceTimeout?: number;
}

const DebounceSelect: FC<DebounceSelectProps> = ({
  url,
  params = {},
  valueKey = 'value',
  labelKey = 'label',
  searchKey = 'search',
  pageSize = 20,
  fetchOptions,
  format,
  debounceTimeout = 300,
  ...props
}) => {
  const [fetching, setFetching] = useState(false);
  const [options, setOptions] = useState<DefaultOptionType[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const pageRef = useRef(1);
  const keywordRef = useRef<string | null>(null);
  const fetchRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout>>();

  const fetchPage = useCallback((keyword: string | null, page: number, replace: boolean) => {
    fetchRef.current += 1;
    const fetchId = fetchRef.current;
    setFetching(true);

    const promise = fetchOptions
      ? fetchOptions(keyword, page)
      : request
          .get<ApiResponse<OptionType>>(url!, { ...params, [searchKey]: keyword, page, pagesize: pageSize })
          .then((res) => {
            const data: OptionType[] = Array.isArray(res) ? res : res?.items || [];
            const formatted = format
              ? format(data)
              : data.map((item) => ({ label: item[labelKey], value: item[valueKey], avatar: item.avatar, raw: item }));

            console.log('more', res.page?.hasnext)
            return { options: formatted, hasMore: res.page?.hasnext };
          });

    promise
      .then(({ options: newOptions, hasMore: more }) => {
        if (fetchId !== fetchRef.current) return;
        setOptions((prev) => (replace ? newOptions : [...prev, ...newOptions]));
        setHasMore(more);
        setFetching(false);
      })
      .catch(() => setFetching(false));
  }, [url, params, searchKey, fetchOptions, format, valueKey, labelKey, pageSize]);

  const debounceFetcher = useCallback((keyword: string | null) => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      keywordRef.current = keyword;
      pageRef.current = 1;
      fetchPage(keyword, 1, true);
    }, debounceTimeout);
  }, [fetchPage, debounceTimeout]);

  useEffect(() => {
    debounceFetcher(null);
  }, []);

  const handlePopupScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    if (!fetching && hasMore && scrollHeight - scrollTop - clientHeight < 50) {
      const nextPage = pageRef.current + 1;
      pageRef.current = nextPage;
      fetchPage(keywordRef.current, nextPage, false);
    }
  }, [fetching, hasMore, fetchPage]);

  return (
    <Select
      labelInValue
      filterOption={false}
      onSearch={debounceFetcher}
      onPopupScroll={handlePopupScroll}
      notFoundContent={fetching ? <Spin size="small" /> : null}
      allowClear
      {...props}
      options={options}
      dropdownRender={(menu) => (
        <>
          {menu}
          {fetching && options.length > 0 && (
            <div style={{ textAlign: 'center', padding: '4px 0' }}><Spin size="small" /></div>
          )}
        </>
      )}
      optionRender={(option) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {option.data.avatar && <Avatar src={option.data.avatar} size="small" />}
          {option.label}
        </div>
      )}
    />
  );
};

export default DebounceSelect;
