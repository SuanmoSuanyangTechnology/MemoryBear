/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:26:44 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:26:44 
 */
/**
 * SliderInput Component
 * 
 * A combined slider and input number component for precise value control:
 * - Synchronized slider and input number
 * - Value range validation
 * - Optional label and marks
 * - Customizable tooltip
 * - Disabled state support
 * 
 * @component
 */

import { useState, useEffect, type FC } from 'react';
import { Slider, InputNumber, Row, Col } from 'antd';

/** Props interface for SliderInput component */
interface SliderInputProps {
  /** Current value */
  value?: number;
  /** Callback fired when value changes */
  onChange?: (value: number | null) => void;
  /** Minimum value */
  min?: number;
  /** Maximum value */
  max?: number;
  /** Step increment */
  step?: number;
  /** Default value */
  defaultValue?: number;
  /** Whether the component is disabled */
  disabled?: boolean;
  /** Optional label text */
  label?: string;
  /** Additional CSS classes for container */
  className?: string;
  /** Additional CSS classes for slider */
  sliderClassName?: string;
  /** Additional CSS classes for input */
  inputClassName?: string;
  /** Marks to display on slider */
  marks?: Record<number, string | { style: React.CSSProperties; label: string }>;
  /** Tooltip configuration */
  tooltip?: {
    open?: boolean;
    placement?: 'top' | 'left' | 'right' | 'bottom';
    formatter?: (value?: number) => React.ReactNode;
  };
}

/** Slider with input number component for precise value control */
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

  /** Sync internal value when external value changes */
  useEffect(() => {
    if (value !== undefined && value !== internalValue) {
      setInternalValue(value);
    }
  }, [value]);

  /** Handle slider value change */
  const handleSliderChange = (newValue: number) => {
    setInternalValue(newValue);
    onChange?.(newValue);
  };

  /** Handle input number value change with range validation */
  const handleInputChange = (newValue: number | null) => {
    if (newValue === null) {
      return;
    }
    
    /** Ensure value is within min/max range */
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
      {/* Optional label */}
      {label && (
        <div className="rb:text-sm rb:font-medium rb:text-gray-700">
          {label}
        </div>
      )}
      <Row gutter={16} align="middle">
        {/* Slider component */}
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
        {/* Input number component */}
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
