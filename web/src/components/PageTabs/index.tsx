import { type FC } from 'react';
import { Segmented, type SegmentedProps } from 'antd';
import styles from './index.module.css';

const PageTabs: FC<SegmentedProps> = ({
  value,
  options,
  onChange
}) => {
  return <Segmented
    value={value}
    options={options}
    onChange={onChange}
    className={styles.pageTabs}
  />;
};

export default PageTabs;
