import { useEffect, useState, type FC, type Key } from 'react';
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
  filterOption?: (inputValue: string, option?: DefaultOptionType) => boolean;
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
  
  // 默认模糊搜索函数
  const defaultFilterOption = (inputValue: string, option?: DefaultOptionType) => {
    if (!option || !inputValue) return true;
    const label = String(option.children || option.label || '');
    return label.toLowerCase().includes(inputValue.toLowerCase());
  };
  // 组件挂载时获取初始数据
  useEffect(() => {
    request.get<ApiResponse<OptionType>>(url, params).then((res) => {
      const data = res;
      setOptions(Array.isArray(data) ? data || [] : Array.isArray(data?.items) ? data.items || [] : []);
    });
  }, []);
  return (
    <Select 
      placeholder={placeholder ? placeholder : t('common.select')} 
      onChange={onChange}
      defaultValue={hasAll ? null : undefined}
      showSearch={showSearch}
      filterOption={filterOption || defaultFilterOption}
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