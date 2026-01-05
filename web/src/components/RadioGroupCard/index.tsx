import { type FC, type Key, type ReactNode, useEffect } from 'react';
import { type RadioGroupProps } from 'antd';
import clsx from 'clsx'

interface RadioCardOption {
  value: string | number | boolean | null | undefined | Key;
  label: string;
  labelDesc?: string;
  icon?: string;
  [key: string]: string | number | boolean | undefined | null | Key;
}

interface RadioCardProps extends Omit<RadioGroupProps, 'onChange'> {
  options: RadioCardOption[];
  onValueChange?: (value: string | null | undefined, option?: RadioCardOption) => void;
  onChange?: (value: string | null | undefined, option?: RadioCardOption) => void;
  itemRender?: (option: RadioCardOption) => ReactNode;
  allowClear?: boolean;
}

const RadioGroupCard: FC<RadioCardProps> = ({
  options,
  value,
  onValueChange,
  onChange,
  itemRender,
  allowClear = true
}) => {
  // 监听value变化
  useEffect(() => {
    if (onValueChange) {
      onValueChange(value);
    }
  }, [value, onValueChange]);

  const handleChange = (option: RadioCardOption) => {
    if (option.disabled) return
    if (onChange) {
      if (allowClear && value === option.value) {
        onChange(null, undefined);
      } else {
        onChange(String(option.value), option);
      }
    }
  }
  
  return (
    <div className={`rb:grid rb:grid-cols-${options.length} rb:gap-3`}>
      {options.map(option => (
          <div key={String(option.value)} className={clsx("rb:border rb:rounded-lg rb:w-full rb:p-[20px_12px] rb:text-center rb:cursor-pointer", {
            'rb:bg-[rgba(21,94,239,0.06)] rb:border-[#155EEF]': option.value === value,
            'rb:border-[#EBEBEB] rb:bg-[#ffffff]': option.value !== value,
            'rb:opacity-[0.75]': option.disabled
          })} onClick={() => handleChange(option)}>
            {itemRender ? itemRender(option) : (
              <>
                {option.icon && <img src={option.icon} className="rb:w-10 rb:h-10 rb:mb-3 rb:m-[0_auto]" />}
                <div className="rb:text-[14px] rb:font-medium">{option.label}</div>
                <div className="rb:mt-1.5 rb:text-[#5B6167] rb:text-[12px] rb:font-regular">{option.labelDesc}</div>
              </>
            )}
          </div>
        )
      )}
    </div>
  );
};

export default RadioGroupCard;