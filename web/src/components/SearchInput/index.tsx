import { useState, type FC, useCallback, useRef } from 'react';
import { Input, type InputProps } from 'antd';
import { useTranslation } from 'react-i18next';
import searchIcon from '@/assets/images/search.svg'

interface SearchInputProps {
  placeholder?: string;
  onSearch?: (value: string) => void;
  debounceDelay?: number;
  throttleDelay?: number;
  defaultValue?: string;
  style?: Record<string, string | number>;
  className?: string;
  size?: InputProps['size']
}

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

  // 防抖函数
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

  // 节流函数
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

  // 处理输入变化
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newValue = e.target.value;
    setValue(newValue);

    // 根据是否设置了throttleDelay来决定使用防抖还是节流
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
      prefix={<img src={searchIcon} alt="search" className="rb:w-4 rb:h-4 rb:mr-1" />}
      placeholder={placeholder || t('user.searchPlaceholder')}
      value={value}
      onChange={handleChange}
      style={{ width: '300px' }}
      className={className}
      {...props}
    />
  );
};

export default SearchInput;