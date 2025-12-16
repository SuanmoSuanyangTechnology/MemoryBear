import { useEffect, useState, useCallback, useRef, type FC, type Key } from 'react';
import { Select } from 'antd'
import type { SelectProps, DefaultOptionType } from 'antd/es/select'
import { useTranslation } from 'react-i18next';
import { request } from '@/utils/request';

// 定义API响应类型
interface ApiResponse<T> {
  items?: T[];
}

interface CustomSelectProps extends Omit<SelectProps, 'filterOption'> {
  url: string;
  params?: Record<string, unknown>;
  valueKey?: string;
  labelKey?: string;
  placeholder?: string;
  hasAll?: boolean;
  allTitle?: string;
  format?: (items: OptionType[]) => OptionType[];
  showSearch?: boolean;
  optionFilterProp?: string;
  // 其他SelectProps属性
  onChange?: SelectProps<Key, DefaultOptionType>['onChange'];
  value?: SelectProps<Key, DefaultOptionType>['value'];
  disabled?: boolean;
  style?: React.CSSProperties;
  className?: string;
  filterOption?: (inputValue: string, option: DefaultOptionType) => boolean;
}
interface OptionType {
  [key: string]: Key | string | number;
}
const CustomSelect: FC<CustomSelectProps> = ({
  onChange,
  url,
  params,
  valueKey = 'value',
  labelKey = 'label',
  placeholder,
  hasAll = true,
  allTitle,
  format,
  showSearch = false,
  optionFilterProp = 'label',
  filterOption,
  ...props
}) => {
  const { t } = useTranslation();
  const [options, setOptions] = useState<OptionType[]>([]); 
  // 创建防抖定时器引用
  const debounceRef = useRef<number>();
  
  // 防抖搜索函数
  const handleSearch = useCallback((value?: string) => {
    // 清除之前的定时器
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    
    // 设置新的定时器
    debounceRef.current = window.setTimeout(() => {
      request.get<ApiResponse<OptionType>>(url, {...params, [optionFilterProp]: value}).then((res) => {
        const data = res;
        setOptions(Array.isArray(data) ? data || [] : Array.isArray(data?.items) ? data.items || [] : []);
      });
    }, 300); // 300毫秒防抖延迟
  }, [url, params, optionFilterProp]);
  
  // 组件挂载时获取初始数据
  useEffect(() => {
    handleSearch();
    
    // 组件卸载时清除定时器
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, [url, handleSearch]);
  return (
    <Select 
      placeholder={placeholder ? placeholder : t('common.select')} 
      onChange={onChange}
      defaultValue={hasAll ? null : undefined}
      showSearch={showSearch}
      onSearch={handleSearch}
      filterOption={filterOption || false} // 禁用本地过滤，使用服务器端过滤
      {...props}
    >
      {hasAll && (<Select.Option>{allTitle || t('common.all')}</Select.Option>)}
      {(format ? format(options) : options)?.map(option => (
        <Select.Option key={option[valueKey]} value={option[valueKey]}>
          {String(option[labelKey])}
        </Select.Option>
      ))}
    </Select>
  );
}
export default CustomSelect;