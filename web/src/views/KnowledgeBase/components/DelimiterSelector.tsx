import { useState, useEffect, type FC } from 'react';
import { Select, Input } from 'antd';
import { useTranslation } from 'react-i18next';
import { DELIMITER_OPTIONS, isCustomDelimiter } from '../constants/delimiter';

interface DelimiterSelectorProps {
  value?: string | null;
  onChange?: (value: string | undefined) => void;
  placeholder?: string;
  className?: string;
}

const DelimiterSelector: FC<DelimiterSelectorProps> = ({
  value,
  onChange,
  placeholder,
  className = '',
}) => {
  const { t } = useTranslation();
  // 默认值为空字符串（不设置）
  const [selectedValue, setSelectedValue] = useState<string>(value || '');
  const [customValue, setCustomValue] = useState<string>('');
  const [showCustomInput, setShowCustomInput] = useState(false);

  useEffect(() => {
    // 检查当前值是否为自定义值
    if (value && isCustomDelimiter(value) && value !== 'custom') {
      setSelectedValue('custom');
      setCustomValue(value);
      setShowCustomInput(true);
    } else {
      setSelectedValue(value || '');
      setShowCustomInput(value === 'custom');
    }
  }, [value]);

  const handleSelectChange = (val: string) => {
    setSelectedValue(val);
    
    if (val === 'custom') {
      setShowCustomInput(true);
      // 如果已有自定义值，使用它；否则等待用户输入
      if (customValue) {
        onChange?.(customValue);
      } else {
        // 自定义但还没输入值，暂不触发 onChange
        onChange?.(undefined);
      }
    } else if (val === '') {
      // 选择"不设置"时，返回 undefined（不传递该参数）
      setShowCustomInput(false);
      onChange?.(undefined);
    } else {
      setShowCustomInput(false);
      onChange?.(val);
    }
  };

  const handleCustomInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setCustomValue(val);
    // 只有当输入不为空时才触发 onChange
    onChange?.(val || undefined);
  };

  return (
    <div className={`rb:flex rb:gap-2 ${className}`}>
      <Select
        value={selectedValue}
        onChange={handleSelectChange}
        placeholder={placeholder || t('knowledgeBase.selectDelimiter') || '请选择分隔符'}
        className='rb:w-full'
        options={DELIMITER_OPTIONS.map(opt => ({
          label: opt.label,
          value: opt.value,
        }))}
      />
      
      {showCustomInput && (
        <Input
          value={customValue}
          onChange={handleCustomInputChange}
          placeholder={t('knowledgeBase.customDelimiterPlaceholder') || '请输入自定义分隔符'}
          maxLength={50}
        />
      )}
    </div>
  );
};

export default DelimiterSelector;
