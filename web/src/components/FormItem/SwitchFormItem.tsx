import { Switch, Form, ConfigProvider } from "antd";
import useSize from 'antd/lib/config-provider/hooks/useSize'
import type { FC, ReactNode } from "react";
import { useContext } from "react";

import LabelWrapper from './LabelWrapper'
import DescWrapper from './DescWrapper'

interface SwitchFormItemProps {
  title: string | ReactNode;
  desc?: string | ReactNode;
  name: string | string[];
  size?: 'small' | 'default'
  className?: string;
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
  const componentSize = useSize()
  console.log('componentSize', componentSize)
  
  return (
    <div className={`${className} rb:flex rb:items-center rb:justify-between`}>
      <LabelWrapper title={title}>
        {desc && <DescWrapper desc={desc} className="rb:mt-2" />}
      </LabelWrapper>
      <Form.Item
        name={name}
        valuePropName="checked"
        className="rb:mb-0!"
      >
        <Switch disabled={disabled} size={size} />
      </Form.Item>
    </div>
  )
}

export default SwitchFormItem