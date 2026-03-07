/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:23:39 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-11 11:10:40
 */
/**
 * RbSlider Component
 * 
 * A custom slider component that extends Ant Design's Slider with:
 * - Value display next to the slider
 * - Value change callback for side effects
 * - Fixed width and custom styling
 * 
 * @component
 */

import { type FC, useEffect, useState } from 'react';
import { Slider, type SliderSingleProps, Flex, InputNumber, type InputNumberProps } from 'antd';

/** Props interface for RbSlider component */
interface RbSliderProps extends SliderSingleProps {
  /** Callback fired when value changes (for side effects) */
  onValueChange?: (value: number | null | undefined) => void;
  /** Callback fired when value changes (for side effects) */
  onChange?: (value: SliderSingleProps['value']) => void;
  isInput?: boolean;
  size?: 'small' | 'default';
  className?: string;
}

/** Custom slider component with value display */
const RbSlider: FC<RbSliderProps> = ({
  value,
  min = 0,
  max,
  onValueChange,
  onChange,
  step = 0.01,
  size = 'default' ,
  isInput = false,
  className = '',
  ...rest
}) => {
  const [curValue, setCurValue] = useState<SliderSingleProps['value']>(0)
  useEffect(() => {
    setCurValue(value)
  }, [value])
  /** Listen to value changes and trigger side effects via onValueChange callback */
  useEffect(() => {
    if (onValueChange) {
      onValueChange(curValue);
    }
  }, [curValue, onValueChange]);
  const handleInputChange: InputNumberProps['onChange'] = (newValue) => {
    onChange?.(newValue as number | undefined);
    setCurValue(newValue as number | undefined)
  };
  const handleSliderChange: SliderSingleProps['onChange'] = (newValue) => {
    onChange?.(newValue);
    setCurValue(newValue)
  };

  return (
    <Flex
      align="center"
      justify="space-between"
      gap={12}
      className={`rb:rounded-[5px] ${className}`}
    >
      {/* Slider with fixed width */}
      <Slider 
        style={{
          overflow: 'inherit',
          width: '384px'
        }}
        {...rest}
        min={min}
        max={max}
        step={step}
        value={curValue}
        onChange={handleSliderChange}
        classNames={size === 'small' ? {
          rail: 'rb:w-[calc(100%-6px)]!'
        } : undefined}
        className={size === 'small' ? `${size} rb:flex-1` : undefined}
      />
      {/* Display current value or minimum value */}
      {isInput
        ? <InputNumber
          min={min}
          max={max}
          step={step as number}
          value={curValue}
          onChange={handleInputChange}
          className="rb:w-20!"
        />
        : <div className="rb:text-[14px] rb:text-[#155EEF] rb:leading-5">{curValue || min}</div>
      }
    </Flex>
  );
};

export default RbSlider;