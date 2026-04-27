/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:18:50 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-21 16:36:54
 */
/**
 * PageTabs Component
 * 
 * A styled wrapper around Ant Design's Segmented component for page-level tab navigation.
 * Provides consistent styling for tab interfaces across the application.
 * 
 * @component
 */

import { type FC } from 'react';
import { Segmented, type SegmentedProps } from 'antd';

/**
 * Page tabs component wrapper for Ant Design Segmented component.
 * Applies custom styling via CSS modules.
 */
const PageTabs: FC<SegmentedProps> = ({
  value,
  options,
  onChange
}) => {
  console.log('value', value)
  return <Segmented
    value={value}
    options={options}
    onChange={onChange}
    className="pageTabs"
  />;
};

export default PageTabs;
