/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:24:23 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-10 14:59:39
 */
/**
 * SearchInput Component
 * 
 * A search input component with debounce and throttle support:
 * - Configurable debounce delay for search optimization
 * - Optional throttle mode for rate limiting
 * - Search icon prefix
 * - Clear button
 * - Internationalized placeholder
 * 
 * @component
 */

import { useState, type FC, useCallback, useRef } from 'react';
import { Input, type InputProps } from 'antd';
import { useTranslation } from 'react-i18next';

/** Props interface for SearchInput component */
interface SearchInputProps {
  /** Placeholder text */
  placeholder?: string;
  /** Callback fired when search value changes */
  onSearch?: (value: string) => void;
  /** Debounce delay in milliseconds (default: 300) */
  debounceDelay?: number;
  /** Throttle delay in milliseconds (overrides debounce if set) */
  throttleDelay?: number;
  /** Default input value */
  defaultValue?: string;
  /** Custom styles */
  style?: Record<string, string | number>;
  /** Additional CSS classes */
  className?: string;
  /** Input size */
  size?: InputProps['size']
  /** Maximum length of the input value */
  maxLength?: number;
}

/** Search input component with debounce and throttle support */
const SearchInput: FC<SearchInputProps> = ({
  placeholder,
  onSearch,
  debounceDelay = 300,
  throttleDelay,
  defaultValue = undefined,
  className = '',
  ...props
}) => {
  const { t } = useTranslation();
  const [value, setValue] = useState(defaultValue);
  const timerRef = useRef<number | null>(null);
  const throttleRef = useRef<boolean>(false);
  const lastCallRef = useRef<number>(0);

  /** Debounce function - delays callback execution until after delay period */
  const debounce = useCallback(<T extends (...args: any[]) => void>(callback: T, delay: number) => {
    return (...args: Parameters<T>) => {
      if (timerRef.current) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        callback(...args);
      }, delay);
    };
  }, []);

  /** Throttle function - limits callback execution to once per delay period */
  const throttle = useCallback(<T extends (...args: any[]) => void>(callback: T, delay: number) => {
    return (...args: Parameters<T>) => {
      const now = Date.now();
      if (!throttleRef.current && now - lastCallRef.current >= delay) {
        lastCallRef.current = now;
        throttleRef.current = true;
        callback(...args);
        window.setTimeout(() => {
          throttleRef.current = false;
        }, delay);
      }
    };
  }, []);

  /** Handle input change with debounce or throttle based on configuration */
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newValue = e.target.value;
    setValue(newValue);

    /** Decide whether to use debounce or throttle based on throttleDelay setting */
    if (onSearch) {
      if (throttleDelay) {
        const throttledSearch = throttle(() => {
          onSearch(newValue);
        }, throttleDelay);
        throttledSearch();
      } else {
        const debouncedSearch = debounce(() => {
          onSearch(newValue);
        }, debounceDelay);
        debouncedSearch();
      }
    }
  };

  return (
    <Input
      allowClear
      prefix={<div className="rb:size-4 rb:bg-[url('@/assets/images/search.svg')] rb:mr-1"></div>}
      placeholder={placeholder || t('user.searchPlaceholder')}
      value={value}
      onChange={handleChange}
      style={{ width: '300px' }}
      className={`rb:border-none! ${className}`}
      {...props}
    />
  );
};

export default SearchInput;