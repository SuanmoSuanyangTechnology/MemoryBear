/**
 * 文档分隔符选项配置
 */

export interface DelimiterOption {
  label: string;
  value: string;
  description?: string;
  displayValue?: string; // 用于显示的值（如果和实际值不同）
}

export const DELIMITER_OPTIONS: DelimiterOption[] = [
  {
    label: '不设置',
    value: '',
    description: '不使用分隔符（不传递该参数）',
  },
  {
    label: '1个换行符',
    value: '\n',
    displayValue: '\\n',
    description: '使用单个换行符作为分隔符',
  },
  {
    label: '2个换行符',
    value: '\n\n',
    displayValue: '\\n\\n',
    description: '使用两个换行符作为分隔符',
  },
  {
    label: '句号',
    value: '。',
    description: '使用句号作为分隔符',
  },
  {
    label: '感叹号',
    value: '！',
    description: '使用感叹号作为分隔符',
  },
  {
    label: '问号',
    value: '？',
    description: '使用问号作为分隔符',
  },
  {
    label: '分号',
    value: '；',
    description: '使用分号作为分隔符',
  },
  {
    label: '=====',
    value: '=====',
    description: '使用五个等号作为分隔符',
  },
  {
    label: '自定义',
    value: 'custom',
    description: '自定义分隔符',
  },
];

// 获取分隔符的显示文本
export const getDelimiterDisplay = (value: string): string => {
  const option = DELIMITER_OPTIONS.find(opt => opt.value === value);
  return option?.displayValue || option?.label || value;
};

// 判断是否为自定义分隔符
export const isCustomDelimiter = (value: string): boolean => {
  return value === 'custom' || !DELIMITER_OPTIONS.some(opt => opt.value === value);
};
