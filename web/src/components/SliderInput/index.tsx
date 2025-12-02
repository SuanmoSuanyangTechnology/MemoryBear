import { useState, useEffect, type FC } from 'react';
import { Slider, InputNumber, Row, Col } from 'antd';

interface SliderInputProps {
  value?: number;
  onChange?: (value: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  defaultValue?: number;
  disabled?: boolean;
  label?: string;
  className?: string;
  sliderClassName?: string;
  inputClassName?: string;
  marks?: Record<number, string | { style: React.CSSProperties; label: string }>;
  tooltip?: {
    open?: boolean;
    placement?: 'top' | 'left' | 'right' | 'bottom';
    formatter?: (value?: number) => React.ReactNode;
  };
}

const SliderInput: FC<SliderInputProps> = ({
  value,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  defaultValue = 0,
  disabled = false,
  label,
  className = '',
  sliderClassName = '',
  inputClassName = '',
  marks,
  tooltip,
}) => {
  const [internalValue, setInternalValue] = useState<number>(value ?? defaultValue);

  useEffect(() => {
    if (value !== undefined && value !== internalValue) {
      setInternalValue(value);
    }
  }, [value]);

  const handleSliderChange = (newValue: number) => {
    setInternalValue(newValue);
    onChange?.(newValue);
  };

  const handleInputChange = (newValue: number | null) => {
    if (newValue === null) {
      return;
    }
    
    // 确保值在范围内
    let validValue = newValue;
    if (newValue < min) {
      validValue = min;
    } else if (newValue > max) {
      validValue = max;
    }
    
    setInternalValue(validValue);
    onChange?.(validValue);
  };

  return (
    <div className={`rb:w-full ${className}`}>
      {label && (
        <div className="rb:text-sm rb:font-medium rb:text-gray-700">
          {label}
        </div>
      )}
      <Row gutter={16} align="middle">
        <Col flex="auto">
          <Slider
            min={min}
            max={max}
            step={step}
            value={internalValue}
            onChange={handleSliderChange}
            disabled={disabled}
            marks={marks}
            tooltip={tooltip}
            className={sliderClassName}
          />
        </Col>
        <Col flex="120px">
          <InputNumber
            min={min}
            max={max}
            step={step}
            value={internalValue}
            onChange={handleInputChange}
            disabled={disabled}
            className={`rb:w-full ${inputClassName}`}
            controls
          />
        </Col>
      </Row>
    </div>
  );
};

export default SliderInput;
