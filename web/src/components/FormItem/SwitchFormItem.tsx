/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:06:24 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-25 14:52:52
 */
/**
 * SwitchFormItem Component
 * 
 * A form item component that combines a switch control with a label and optional description.
 * Provides a consistent layout for switch-based form fields.
 * 
 * @component
 */

import { Switch, Form, Flex } from "antd";
import type { FC, ReactNode } from "react";

import LabelWrapper from './LabelWrapper'
import DescWrapper from './DescWrapper'

interface SwitchFormItemProps {
  /** Label text or React node */
  title: string | ReactNode;
  /** Optional description text or React node */
  desc?: string | ReactNode;
  /** Form field name (string or nested path array) */
  name: string | string[];
  /** Switch size */
  size?: 'small' | 'default'
  /** Additional CSS classes */
  className?: string;
  /** Whether the switch is disabled */
  disabled?: boolean;
}

const SwitchFormItem: FC<SwitchFormItemProps> = ({
  title,
  desc,
  name,
  size = 'default',
  className,
  disabled
}) => {
  return (
    <Flex
      align="center"
      justify="space-between"
      className={className}
    >
      {/* Label and description section */}
      <LabelWrapper title={title}>
        {desc && <DescWrapper desc={desc} className="rb:mt-1" />}
      </LabelWrapper>
      {/* Switch control */}
      <Form.Item
        name={name}
        valuePropName="checked"
        className="rb:mb-0!"
      >
        <Switch disabled={disabled} size={size} />
      </Form.Item>
    </Flex>
  )
}

export default SwitchFormItem