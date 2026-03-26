/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:19:30 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-03 11:09:23
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
  onChange?: (value: string | number | boolean | null | undefined | Key, option?: RadioCardOption) => void;
  /** Custom render function for each option */
  itemRender?: (option: RadioCardOption) => ReactNode;
  /** Whether clicking selected option clears selection */
  allowClear?: boolean;
  /** Whether to display cards in block (vertical) layout */
  block?: boolean;
  type?: 'inner';
}

/** Radio group card component that displays options as selectable cards */
const RadioGroupBtn: FC<RadioCardProps> = ({  
  options,
  value,  
  onValueChange,
  onChange,
  allowClear = true,
  block = false,
  type,
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
        onChange(option.value, option);
      }
    }
  }

  return (
    <div className={clsx(`rb:grid rb:grid-cols-${block ? 1 : options.length} rb:gap-1`)}>
      {/* Render each option as a selectable card */}
      {options.map(option => (
        <div key={String(option.value)} className={clsx("rb:border rb:w-full rb:leading-4.5 rb:px-2.5  rb:text-center rb:text-[12px] rb:font-medium rb:cursor-pointer", {
          'rb:opacity-[0.75]': option.disabled,
          'rb:rounded-lg rb:bg-[#F6F6F6] rb:border-[#F6F6F6] rb:py-1.25': !type,
          'rb:bg-white rb:rounded-md rb:border-white rb:py-px': type === 'inner',
          'rb:border-[#171719]! rb:bg-white': option.value === value,
        })} onClick={() => handleChange(option)}>
          {option.label}
        </div>
      ))}
    </div>
  );
};

export default RadioGroupBtn;
