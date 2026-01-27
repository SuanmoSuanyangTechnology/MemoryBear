import { useEffect, useState, type FC, type Key } from 'react';
import { Select } from 'antd';
import type { SelectProps, DefaultOptionType } from 'antd/es/select';
import { useTranslation } from 'react-i18next';
import { request } from '@/utils/request';

interface OptionType {
  [key: string]: Key | string | number;
}

interface ApiResponse<T> {
  items?: T[];
}

interface CustomSelectProps extends Omit<SelectProps, 'filterOption'> {
  url: string;
  params?: Record<string, unknown>;
  valueKey?: string | string[];
  labelKey?: string;
  placeholder?: string;
  hasAll?: boolean;
  allTitle?: string;
  format?: (items: OptionType[]) => OptionType[];
  showSearch?: boolean;
  optionFilterProp?: string;
  filterOption?: (inputValue: string, option?: DefaultOptionType) => boolean;
}

const defaultFilterOption = (inputValue: string, option?: DefaultOptionType): boolean => {
  if (!option || !inputValue) return true;
  const label = String(option.children || option.label || '');
  return label.toLowerCase().includes(inputValue.toLowerCase());
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

  useEffect(() => {
    request.get<ApiResponse<OptionType>>(url, params).then((res) => {
      const data = Array.isArray(res) ? res : res?.items || [];
      setOptions(data);
    });
  }, [url, params]);

  const displayOptions = format ? format(options) : options;

  return (
    <Select
      placeholder={placeholder || t('common.select')}
      defaultValue={hasAll ? null : undefined}
      showSearch={showSearch}
      filterOption={filterOption || defaultFilterOption}
      {...props}
    >
      {hasAll && <Select.Option value={null}>{allTitle || t('common.all')}</Select.Option>}
      {displayOptions.map((option) => {
        const getValue = () => {
          if (typeof valueKey === 'string') return option[valueKey];
          return valueKey.find(key => option[key] != null) ? option[valueKey.find(key => option[key] != null)!] : undefined;
        };
        const value = getValue();
        return (
          <Select.Option key={value} value={value}>
            {String(option[labelKey])}
          </Select.Option>
        );
      })}
    </Select>
  );
};

export default CustomSelect;