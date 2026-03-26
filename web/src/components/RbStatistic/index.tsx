/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-16 14:52:06 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-16 14:52:06 
 */
import { type FC } from 'react';

/**
 * Props for the RbStatistic component.
 * @property title - The label displayed above the statistic value.
 * @property value - The numeric or string value to display.
 * @property suffix - Optional unit or suffix appended after the value (e.g. "%", "items").
 */
interface RbStatistic {
  title: string;
  value: number | string;
  suffix?: string;
}

/**
 * RbStatistic – A lightweight statistic display component.
 *
 * Renders a title/label on top and a prominent value (with an optional suffix)
 * below it. Commonly used in dashboard cards and summary panels.
 *
 * @example
 * <RbStatistic title="Total Users" value={1280} suffix="people" />
 */
const RbStatistic: FC<RbStatistic> = ({
  title,
  value,
  suffix,
}) => {
  return (
    <div className="rb:leading-5">
      <div className="rb:text-[#5B6167] rb:mb-1">{title}</div>
      <div className="rb:text-[#212332] rb:font-medium">{value} {suffix}</div>
    </div>
  );
};

export default RbStatistic