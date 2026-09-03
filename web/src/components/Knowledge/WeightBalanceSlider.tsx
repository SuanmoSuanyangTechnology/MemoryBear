/**
 * WeightBalanceSlider Component
 * 
 * A balance slider that distributes weight between two dimensions (semantic / keyword).
 * semantic_weight + participle_weight = 1.0
 */

import { useEffect, useState } from 'react';
import type { FC } from 'react';
import { Slider, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

interface WeightBalanceSliderProps {
  semanticWeight?: number;
  participleWeight?: number;
  onChange: (semanticWeight: number, participleWeight: number) => void;
  step?: number;
}

const WeightBalanceSlider: FC<WeightBalanceSliderProps> = ({
  semanticWeight,
  participleWeight,
  onChange,
  step = 0.1,
}) => {
  const { t } = useTranslation();
  const [value, setValue] = useState<number>(0.5);

  // Sync with external values (semantic_weight is the source of truth)
  useEffect(() => {
    if (semanticWeight !== undefined && semanticWeight !== null) {
      setValue(semanticWeight);
    } else if (participleWeight !== undefined && participleWeight !== null) {
      setValue(1 - participleWeight);
    }
  }, [semanticWeight, participleWeight]);

  const handleChange = (newValue: number) => {
    setValue(newValue);
    onChange(Math.round(newValue * 100) / 100, Math.round((1 - newValue) * 100) / 100);
  };

  const formattedValue = Number(value.toFixed(1));
  const formattedOpposite = Number((1 - value).toFixed(1));

  return (
    <div className="rb:w-full rb:px-2">
      <Slider
        min={0}
        max={1}
        step={step}
        value={value}
        onChange={handleChange}
        className="rb:my-0!"
        classNames={{
          rail: 'rb:h-[6px]!',
          track: 'rb:h-[6px]!',
        }}
        styles={{
          rail: {
            background: '#369F21',
          },
          track: {
            background: '#155EEF',
          },
        }}
      />
      <Flex align="center" justify="space-between" className="rb:px-1! rb:text-[12px]">
        <Flex gap={4} className="rb:text-blue-500">
          <span>{t('application.semantic_label')}</span>
          <span className="rb:font-semibold">{formattedValue}</span>
        </Flex>
        <Flex gap={4} className="rb:text-green-600">
          <span className="rb:font-semibold">{formattedOpposite}</span>
          <span>{t('application.keyword_label')}</span>
        </Flex>
      </Flex>
    </div>
  );
};

export default WeightBalanceSlider;
