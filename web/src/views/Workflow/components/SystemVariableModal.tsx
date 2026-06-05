/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-28 14:04:32 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-28 14:04:32 
 */
import { useState, useImperativeHandle, forwardRef } from 'react';
import { List, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import RbDrawer from '@/components/RbDrawer';
import { sysVariable } from './Properties/hooks/useVariableList';
import type { Application } from '@/views/ApplicationManagement/types';

export interface SystemVariableModalRef {
  handleOpen: () => void;
}

const SystemVariableModal = forwardRef<SystemVariableModalRef, { appType?: Application['type'] }>(({ appType }, ref) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const handleOpen = () => {
    setOpen(true)
  }
  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
      handleOpen,
  }));

  return (
    <RbDrawer
      title={t('workflow.systemVariable')}
      open={open}
      onClose={() => setOpen(false)}
      width={480}
    >
      <List
        grid={{ gutter: 12, column: 1 }}
        dataSource={appType === 'pure_workflow' ? sysVariable.filter(item => item.name !== 'message') : sysVariable}
        renderItem={(item, index) => (
          <List.Item key={index}>
            <Flex align="center" justify="space-between" className="rb:leading-4 rb:relative rb:p-[12px_16px]! rb:bg-[#FBFDFF] rb:cursor-pointer rb-border rb:rounded-lg">
              <div className="rb:flex-1 rb:font-medium rb:whitespace-break-spaces rb:wrap-break-word rb:line-clamp-1">sys.{item.name}</div>
              <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular"> ({t(`workflow.config.parameter-extractor.${item.type}`)})</div>
            </Flex>
          </List.Item>
        )}
      />
    </RbDrawer>
  );
});

export default SystemVariableModal;