import React, { useState, useEffect } from 'react';
import type { InputNumberProps, SliderSingleProps } from 'antd';
import { Col, InputNumber, Row, Slider } from 'antd';

const RbSlider: React.FC<SliderSingleProps> = ({
  value,
  onChange,
  min,
  max,
  step = 0.01,
  ...props
}) => {
  const [curValue, setCurValue] = useState<number | undefined>(0)
  useEffect(() => {
    setCurValue(value)
  }, [value])
  const handleSliderChange = (newValue: number) => {
    onChange && onChange(newValue);
  };

  const handleInputChange: InputNumberProps['onChange'] = (newValue) => {
    onChange && onChange(newValue as number);
  };

  return (
    <Row gutter={12}>
      <Col span={16}>
        <Slider
          {...props}
          min={min}
          max={max}
          step={step as number}
          value={curValue}
          className="rb:my-0! rb:ml-2.5!"
          classNames={{
            rail: 'rb:h-[6px]!',
            track: 'rb:h-[6px]!'
          }}
          onChange={handleSliderChange}
        />
      </Col>
      <Col span={8}>
        <InputNumber
          min={min}
          max={max}
          step={step as number}
          value={curValue}
          onChange={handleInputChange}
          className="rb:w-full!"
        />
      </Col>
    </Row>
  );
};
export default RbSlider;