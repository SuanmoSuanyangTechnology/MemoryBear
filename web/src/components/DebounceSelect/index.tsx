import { useRef, useState, useCallback, useEffect, type FC } from 'react';
import { Select, Spin, Avatar } from 'antd';
import type { SelectProps, DefaultOptionType } from 'antd/es/select';

import { request } from '@/utils/request';

interface OptionType {
  [key: string]: any;
}

interface ApiResponse<T> {
  items?: T[];
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
  /** Custom fetch function — mutually exclusive with url */
  fetchOptions?: (search: string | null) => Promise<DefaultOptionType[]>;
  /** Transform raw API items before rendering */
  format?: (items: OptionType[]) => OptionType[];
  debounceTimeout?: number;
}

const DebounceSelect: FC<DebounceSelectProps> = ({
  url,
  params = { page: 1, pagesize: 20 },
  valueKey = 'value',
  labelKey = 'label',
  searchKey = 'search',
  fetchOptions,
  format,
  debounceTimeout = 300,
  ...props
}) => {
  const [fetching, setFetching] = useState(false);
  const [options, setOptions] = useState<DefaultOptionType[]>([]);
  const fetchRef = useRef(0);

  const timerRef = useRef<ReturnType<typeof setTimeout>>();

  // Load initial options on mount
  useEffect(() => {
    debounceFetcher(null);
  }, []);

  const debounceFetcher = useCallback((keyword: string | null) => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      fetchRef.current += 1;
      const fetchId = fetchRef.current;
      setOptions([]);
      setFetching(true);

      const promise: Promise<DefaultOptionType[]> = fetchOptions
        ? fetchOptions(keyword)
        : request
            .get<ApiResponse<OptionType>>(url!, { ...params, [searchKey]: keyword })
            .then((res) => {
              const data: OptionType[] = Array.isArray(res) ? res : res?.items || [];
              const formatted = format ? format(data) : data.map((item) => ({
                label: item[labelKey],
                value: item[valueKey],
                avatar: item.avatar,
                raw: item,
              }));
              return formatted;
            });

      promise
        .then((newOptions) => {
          if (fetchId !== fetchRef.current) return;
          setOptions(newOptions);
          setFetching(false);
        })
        .catch(() => setFetching(false));
    }, debounceTimeout);
  }, [url, params, searchKey, fetchOptions, format, valueKey, labelKey, debounceTimeout]);

  return (
    <Select
      labelInValue
      filterOption={false}
      onSearch={debounceFetcher}
      notFoundContent={fetching ? <Spin size="small" /> : null}
      {...props}
      options={options}
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
