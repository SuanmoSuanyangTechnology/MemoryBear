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
  // Default value is empty string (not set)
  const [selectedValue, setSelectedValue] = useState<string>(value || '');
  const [customValue, setCustomValue] = useState<string>('');
  const [showCustomInput, setShowCustomInput] = useState(false);

  useEffect(() => {
    // Check if current value is a custom delimiter
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
      // If custom value exists, use it; otherwise wait for user input
      if (customValue) {
        onChange?.(customValue);
      } else {
        // Custom selected but no value entered yet, don't trigger onChange
        onChange?.(undefined);
      }
    } else if (val === '') {
      // When "Not set" is selected, return undefined (don't pass this parameter)
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
    // Only trigger onChange when input is not empty
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
