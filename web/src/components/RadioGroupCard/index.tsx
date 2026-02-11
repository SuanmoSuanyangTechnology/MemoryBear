/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:19:30 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:19:30 
 */
/**
 * RadioGroupCard Component
 * 
 * A radio group component that displays options as selectable cards with:
 * - Visual card-based selection interface
 * - Optional icons and descriptions
 * - Support for clear selection
 * - Block or inline layout modes
 * - Custom item rendering
 * 
 * @component
 */

import { type FC, type Key, type ReactNode, useEffect } from 'react';
import { type RadioGroupProps } from 'antd';
import clsx from 'clsx'

/** Radio card option interface */
interface RadioCardOption {
  /** Option value */
  value: string | number | boolean | null | undefined | Key;
  /** Option label text */
  label: string;
  /** Optional description text */
  labelDesc?: string;
  /** Optional icon URL */
  icon?: string;
  /** Whether the option is disabled */
  disabled?: boolean;
  /** Additional properties */
  [key: string]: string | number | boolean | undefined | null | Key;
}

/** Props interface for RadioGroupCard component */
interface RadioCardProps extends Omit<RadioGroupProps, 'onChange'> {
  /** Array of radio card options */
  options: RadioCardOption[];
  /** Callback fired when value changes (for side effects) */
  onValueChange?: (value: string | null | undefined, option?: RadioCardOption) => void;
  /** Callback fired when selection changes */
  onChange?: (value: string | null | undefined, option?: RadioCardOption) => void;
  /** Custom render function for each option */
  itemRender?: (option: RadioCardOption) => ReactNode;
  /** Whether clicking selected option clears selection */
  allowClear?: boolean;
  /** Whether to display cards in block (vertical) layout */
  block?: boolean;
}

/** Radio group card component that displays options as selectable cards */
const RadioGroupCard: FC<RadioCardProps> = ({
  options,
  value,
  onValueChange,
  onChange,
  itemRender,
  allowClear = true,
  block = false,
}) => {
  /** Listen to value changes and trigger side effects via onValueChange callback */
  useEffect(() => {
    if (onValueChange) {
      onValueChange(value);
    }
  }, [value, onValueChange]);

  /** Handle option selection with support for clear and disabled states */
  const handleChange = (option: RadioCardOption) => {
    // Ignore clicks on disabled options
    if (option.disabled) return
    if (onChange) {
      // Clear selection if allowClear is true and option is already selected
      if (allowClear && value === option.value) {
        onChange(null, undefined);
      } else {
        onChange(String(option.value), option);
      }
    }
  }
  
  return (
    <div className={clsx(`rb:grid rb:grid-cols-${block ? 1 : options.length}`, {
      'rb:gap-3': !block,
      'rb:gap-4': block,
    })}>
      {/* Render each option as a selectable card */}
      {options.map(option => (
        <div key={String(option.value)} className={clsx("rb:border rb:rounded-lg rb:w-full rb:p-[20px_12px] rb:text-center rb:cursor-pointer", {
          'rb:bg-[rgba(21,94,239,0.06)] rb:border-[#155EEF]': option.value === value,
          'rb:border-[#EBEBEB] rb:bg-[#ffffff]': option.value !== value,
          'rb:opacity-[0.75]': option.disabled,
          'rb:flex rb:items-center rb:text-left rb:gap-4': block,
        })} onClick={() => handleChange(option)}>
          {/* Use custom render or default card layout */}
          {itemRender ? itemRender(option) : (
            <>
              {option.icon && <img src={option.icon} className={clsx("rb:w-10 rb:h-10", {
                'rb:m-[0_auto] rb:mb-3': !block,
              })} />}
              <div>
                <div className="rb:text-[14px] rb:font-medium">{option.label}</div>
                <div className="rb:mt-1.5 rb:text-[#5B6167] rb:text-[12px] rb:font-regular">{option.labelDesc}</div>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  );
};

export default RadioGroupCard;