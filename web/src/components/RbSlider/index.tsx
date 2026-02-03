/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:23:39 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:23:39 
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

import { type FC, useEffect } from 'react';
import { Slider, type SliderSingleProps } from 'antd';

/** Props interface for RbSlider component */
interface RbSliderProps extends SliderSingleProps {
  /** Callback fired when value changes (for side effects) */
  onValueChange?: (value: number | null | undefined) => void;
}

/** Custom slider component with value display */
const RbSlider: FC<RbSliderProps> = ({
  value,
  min = 0,
  onValueChange,
  step = 1,
  ...rest
}) => {
  /** Listen to value changes and trigger side effects via onValueChange callback */
  useEffect(() => {
    if (onValueChange) {
      onValueChange(value);
    }
  }, [value, onValueChange]);

  return (
    <div className="rb:flex rb:items-center rb:justify-between rb:gap-2 rb:rounded-[5px]">
      {/* Slider with fixed width */}
      <Slider 
        style={{
          overflow: 'inherit',
          width: '384px'
        }}
        {...rest}
        step={step}
        value={value}
      />
      {/* Display current value or minimum value */}
      <div className="rb:text-[14px] rb:text-[#155EEF] rb:leading-5">{value || min}</div>
    </div>
  );
};

export default RbSlider;