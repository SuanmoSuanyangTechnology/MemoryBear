/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:02:17 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-04 14:43:42
 */
/**
 * CustomSelect - A select component that fetches options from an API
 * 
 * This component extends Ant Design's Select with automatic data fetching,
 * search functionality, and customizable option formatting.
 * 
 * @component
 */

import { isValidElement, useEffect, useState, useMemo, type FC, type ReactNode } from 'react';
import { Select } from 'antd';
import type { SelectProps, DefaultOptionType } from 'antd/es/select';
import { useTranslation } from 'react-i18next';

import { request } from '@/utils/request';

// Generic option type for API response data
export interface OptionType {
  [key: string]: any;
}

// API response structure
interface ApiResponse<T> {
  items?: T[];
}

interface CustomSelectProps extends Omit<SelectProps, 'filterOption'> {
  /** API endpoint URL to fetch options */
  url: string;
  /** Query parameters for the API request */
  params?: Record<string, unknown>;
  /** Key name for option value in response data */
  valueKey?: string;
  /** Key name for option label in response data */
  labelKey?: string;
  /** Placeholder text for the select */
  placeholder?: string;
  /** Whether to show "All" option */
  hasAll?: boolean;
  /** Custom text for "All" option */
  allTitle?: string;
  /** Function to format/transform the options data */
  format?: (items: OptionType[]) => OptionType[];
  /** Whether to enable search functionality */
  showSearch?: boolean;
  /** Property name to filter options by */
  optionFilterProp?: string;
  /** Custom filter function for search */
  filterOption?: (inputValue: string, option?: DefaultOptionType) => boolean;
}

// Extract searchable text from plain values and nested React elements
const getTextContent = (content: ReactNode): string => {
  if (typeof content === 'string' || typeof content === 'number') {
    return String(content);
  }
  if (Array.isArray(content)) {
    return content.map(getTextContent).join(' ');
  }
  if (isValidElement<{ children?: ReactNode }>(content)) {
    return getTextContent(content.props.children);
  }
  return '';
};

// Default filter function for search - matches label and value case-insensitively
const defaultFilterOption = (inputValue: string, option?: DefaultOptionType): boolean => {
  const keyword = inputValue.trim().toLowerCase();
  if (!keyword || !option) return true;

  const label = getTextContent(option.label) || getTextContent(option.children);
  const value = String(option.value ?? '');
  return label.toLowerCase().includes(keyword) || value.toLowerCase().includes(keyword);
};

const CustomSelect: FC<CustomSelectProps> = ({
  url,
  params,
  valueKey = 'value',
  labelKey = 'label',
  placeholder,
  hasAll = true,
  allTitle,
  format,
  showSearch = false,
  filterOption,
  ...props
}) => {
  const { t } = useTranslation();
  const [options, setOptions] = useState<OptionType[]>([]);
  // Memoize params to prevent unnecessary re-fetches
  const memoizedParams = useMemo(() => params, [JSON.stringify(params)]);

  // Fetch options from API when url or params change
  useEffect(() => {
    request.get<ApiResponse<OptionType>>(url, memoizedParams).then((res) => {
      const data = Array.isArray(res) ? res : res?.items || [];
      setOptions(data);
    });
  }, [url, memoizedParams]);

  // Apply custom format function if provided
  const displayOptions = format ? format(options) : options;

  return (
    <Select
      placeholder={placeholder || t('common.select')}
      defaultValue={hasAll ? null : undefined}
      showSearch={showSearch}
      filterOption={filterOption || defaultFilterOption}
      {...props}
    >
      {/* Optional "All" option for selecting all items */}
      {hasAll && <Select.Option value={null}>{allTitle || t('common.all')}</Select.Option>}
      {/* Render options from API data */}
      {displayOptions.map((option) => (
        <Select.Option key={option[valueKey]} value={option[valueKey]}>
          {option[labelKey]}
        </Select.Option>
      ))}
    </Select>
  );
};

export default CustomSelect;