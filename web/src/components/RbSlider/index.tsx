import { type FC, useEffect } from 'react';
import { Slider, type SliderSingleProps } from 'antd';

interface RbSliderProps extends SliderSingleProps {
  onValueChange?: (value: number | null | undefined) => void;
}

const RbSlider: FC<RbSliderProps> = ({
  value,
  min = 0,
  onValueChange,
  step = 1,
  ...rest
}) => {
  // 监听value变化，包括初始值
  useEffect(() => {
    if (onValueChange) {
      onValueChange(value);
    }
  }, [value, onValueChange]);
 
  // const flag1 = value && value > (min + step * 1)
  // const flag = value && value > (min + step * 1)
  return (
    <div className="rb:flex rb:items-center rb:justify-between rb:gap-[8px] rb:rounded-[5px]">
      <Slider 
        style={{
          // width: flag1 ? '384px' : '373px',
          // margin: flag ? '0 11px 0 0': '0 5px 0 11px'
          overflow: 'inherit',
          width: '384px'
        }}
        {...rest}
        step={step}
        value={value}
      />
      <div className="rb:text-[14px] rb:text-[#155EEF] rb:leading-[20px]">{value || min}</div>
    </div>
  );
};

export default RbSlider;