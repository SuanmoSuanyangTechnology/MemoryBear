/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-27 15:00:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-27 17:13:29
 */
/**
 * CheckboxGroupButton Component
 *
 * A pill / chip-style checkbox group that renders each option as a small
 * rounded tag. Selected options are highlighted with a dark background
 * and white text; unselected options use a light grey background.
 *
 * Features:
 * - Pill-shaped selectable tags laid out horizontally via Ant Design Space
 * - Multiple selection support
 * - Optional "allowClear" mode: clicking the active option deselects it
 * - Optional "allowEmpty" mode: allows all options to be deselected
 * - Disabled state per option
 * - Two callbacks: onChange (controlled value) and onValueChange (side-effect)
 *
 * @component
 */

import { type FC, type Key, type ReactNode, useEffect } from 'react';
import clsx from 'clsx'

/** Describes a single selectable option within the checkbox group. */
interface CheckboxCardOption {
  /** Unique value that identifies this option. */
  value: string | number | boolean | null | undefined | Key;
  /** Display content rendered inside the pill tag. */
  label: string | ReactNode;
  /** When true the option is visually muted and cannot be selected. */
  disabled?: boolean;
  span?: number;
}

/** Props for the CheckboxGroupButton component. */
interface CheckboxCardProps {
  /** List of selectable options to render as pill tags. */
  options: CheckboxCardOption[];
  /** Controlled array of selected values. */
  value?: (string | number | boolean | null | undefined | Key)[];
  /** Side-effect callback invoked whenever the value changes (including on mount). */
  onValueChange?: (value: (string | null | undefined)[]) => void;
  /** Controlled callback invoked when the user clicks an option. */
  onChange?: (value: (string | null | undefined)[], option?: CheckboxCardOption) => void;
  /** If true, clicking the already-selected option will deselect it. */
  allowClear?: boolean;
  /** If true, allows all options to be deselected (empty selection). */
  allowEmpty?: boolean;
  /** Size of the checkbox buttons. */
  size?: 'default' | 'small';
  /** Type of the checkbox buttons. */
  type?: 'inner' | 'outer';
  /** Additional CSS class name. */
  className?: string;
  grid?: number;
}

/** Renders a horizontal row of pill-shaped checkbox options. */
const CheckboxGroupButton: FC<CheckboxCardProps> = ({
  options,
  value = [],
  onValueChange,
  onChange,
  allowClear = true,
  allowEmpty = true,
  size = 'default',
  type = 'outer',
  className,
  grid
}) => {
  /* Notify parent of value changes (useful for side-effects like analytics). */
  useEffect(() => {
    if (onValueChange) {
      onValueChange(value.map(v => String(v)));
    }
  }, [value, onValueChange]);

  /* Toggle selection; supports allowClear and respects disabled state. */
  const handleChange = (option: CheckboxCardOption) => {
    // Ignore clicks on disabled options
    if (option.disabled) return
    
    const stringValue = String(option.value);
    const currentValues = value.map(v => String(v));
    const isSelected = currentValues.includes(stringValue);
    
    let newValue: (string | null | undefined)[];
    
    if (isSelected) {
      if (!allowClear) return;
      // Deselect the option
      newValue = currentValues.filter(v => v !== stringValue);
      // If allowEmpty is false and this would leave nothing selected, do nothing
      if (!allowEmpty && newValue.length === 0) return;
    } else {
      // Select the option
      newValue = [...currentValues, stringValue];
    }
    
    if (onChange) {
      onChange(newValue, option);
    }
  }
  
  return (
    <div className={`rb:grid rb:grid-cols-${grid || options.length} rb:gap-1 ${className || ''}`}>
      {options.map(option => {
        const stringValue = String(option.value);
        const isSelected = value.map(v => String(v)).includes(stringValue);
        
        return (
          <div
            key={stringValue}
            className={clsx(
              'rb:leading-4.5 rb:text-[12px] rb:text-center rb:cursor-pointer rb:rounded-lg',
              {
                'rb:py-1.5 rb:px-2': size === 'default',
                'rb:py-1': size === 'small',
                'rb:border rb:border-[#EBEBEB] rb:hover:border-[#171719]': type === 'outer',
                'rb:border rb:border-[#F6F6F6] rb:bg-[#F6F6F6] rb:hover:bg-[#FFFFFF] rb:hover:border-[#171719]': type === 'inner',
                'rb:border-[#171719]!': type === 'outer' && isSelected,
                'rb:bg-white rb:hover:bg-white rb:border-[#171719]!': type === 'inner' && isSelected,
                'rb:opacity-50 rb:cursor-not-allowed': option.disabled,
              },
              option.span ? `rb:col-span-${option.span}` : ''
            )}
            onClick={() => handleChange(option)}
          >
            {option.label}
          </div>
        );
      })}
    </div>
  );
};

export default CheckboxGroupButton;
