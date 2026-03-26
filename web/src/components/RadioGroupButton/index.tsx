/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-16 14:53:33 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-16 14:53:33 
 */
/**
 * RadioGroupButton Component
 *
 * A pill / chip-style radio group that renders each option as a small
 * rounded tag. The selected option is highlighted with a dark background
 * and white text; unselected options use a light grey background.
 *
 * Features:
 * - Pill-shaped selectable tags laid out horizontally via Ant Design Space
 * - Optional "allowClear" mode: clicking the active option deselects it
 * - Disabled state per option
 * - Two callbacks: onChange (controlled value) and onValueChange (side-effect)
 *
 * @component
 */

import { type FC, type Key, type ReactNode, useEffect } from 'react';
import { type RadioGroupProps, Space } from 'antd';
import clsx from 'clsx'

/** Describes a single selectable option within the radio group. */
interface RadioCardOption {
  /** Unique value that identifies this option. */
  value: string | number | boolean | null | undefined | Key;
  /** Display content rendered inside the pill tag. */
  label: string | ReactNode;
  /** When true the option is visually muted and cannot be selected. */
  disabled?: boolean;
}

/** Props for the RadioGroupButton component. */
interface RadioCardProps extends Omit<RadioGroupProps, 'onChange'> {
  /** List of selectable options to render as pill tags. */
  options: RadioCardOption[];
  /** Side-effect callback invoked whenever the value changes (including on mount). */
  onValueChange?: (value: string | null | undefined, option?: RadioCardOption) => void;
  /** Controlled callback invoked when the user clicks an option. */
  onChange?: (value: string | null | undefined, option?: RadioCardOption) => void;
  /** If true, clicking the already-selected option will deselect it (set value to null). */
  allowClear?: boolean;
}

/** Renders a horizontal row of pill-shaped radio options. */
const RadioGroupButton: FC<RadioCardProps> = ({
  options,
  value,
  onValueChange,
  onChange,
  allowClear = false,
}) => {
  /* Notify parent of value changes (useful for side-effects like analytics). */
  useEffect(() => {
    if (onValueChange) {
      onValueChange(value);
    }
  }, [value, onValueChange]);

  /* Toggle selection; supports allowClear and respects disabled state. */
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
    <Space size={12}>
      {options.map(option => (
        <div
          key={String(option.value)}
          className={clsx('rb:rounded-[14px] rb:py-1 rb:px-2 rb:text-[12px] rb:leading-4.5 rb:cursor-pointer', {
            'rb:bg-[#171719] rb:font-medium rb:text-white': value === option.value,
            'rb:bg-[#F6F6F6]': value !== option.value,
          })}
          onClick={() => handleChange(option)}
        >
          {option.label}
        </div>
      ))}
    </Space>
  );
};

export default RadioGroupButton;