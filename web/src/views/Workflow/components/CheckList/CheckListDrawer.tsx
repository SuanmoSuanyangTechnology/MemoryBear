/*
 * @Description: CheckList drawer component
 * @Version: 0.0.1
 * @Author: ZhaoYing
 * @Date: 2026-06-12
 */
import { useState, useImperativeHandle, forwardRef } from 'react'
import { Flex } from 'antd'
import { WarningFilled } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { Node } from '@antv/x6';

import type { WorkflowRef } from '@/views/ApplicationConfig/types'
import type { NodeCheckResult } from '.'
import RbDrawer from '@/components/RbDrawer'

export interface CheckListDrawerRef {
  handleOpen: () => void;
}

interface CheckListDrawerProps {
  results: NodeCheckResult[];
  workflowRef: React.RefObject<WorkflowRef>;
}

const CheckListDrawer = forwardRef<CheckListDrawerRef, CheckListDrawerProps>(({ results, workflowRef }, ref) => {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const errorCount = results.reduce((sum, n) => sum + n.errors.length, 0)

  const handleOpen = () => {
    setOpen(true)
  }

  const handleClose = () => {
    setOpen(false)
  }
  const focusNode = (id: string) => {
    const graph = workflowRef.current?.graphRef?.current
    if (!graph) return
    const node = graph.getCellById(id)
    if (node) {
      workflowRef.current?.nodeClick({node} as { node: Node })
    }
    setOpen(false)
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbDrawer
      title={
        <span className="rb:text-[16px] rb:font-semibold">
          {t('workflow.checkList')}{errorCount > 0 ? `(${errorCount})` : ''}
        </span>
      }
      open={open}
      onClose={handleClose}
      width={360}
      styles={{ body: { padding: '12px 16px' } }}
    >
      <p className="rb:text-[12px] rb:text-[#5B6167] rb:mb-3">{t('workflow.checkListDesc')}</p>
      {results.length === 0
          ? <div className="rb:text-center rb:text-[#5B6167] rb:text-[13px] rb:py-8">{t('workflow.checkListEmpty')}</div>
        : <Flex vertical gap={8} className="rb:pb-3!">
            {results.map(node => (
              <div key={node.id} className="rb-border rb:rounded-lg">
                <Flex align="center" gap={8} className="rb:px-3! rb:py-2.5! rb-border-b">
                  <div className={`rb:size-5 rb:rounded-md rb:bg-size-[14px_14px] rb:bg-center rb:bg-no-repeat ${node.icon}`} />
                  <span className="rb:text-[13px] rb:font-medium rb:flex-1 rb:truncate">{node.name}</span>
                  <span
                    className="rb:text-[12px] rb:text-[#155EEF] rb:cursor-pointer rb:whitespace-nowrap"
                    onClick={() => focusNode(node.id)}
                  >
                    {t('workflow.goto')} →
                  </span>
                </Flex>

                <Flex vertical gap={4} className="rb:px-3! rb:py-2!">
                  {node.errors.map((err, i) => (
                    <Flex key={i} align="center" gap={6}>
                      <WarningFilled className="rb:text-[#FF5D34]! rb:text-[12px] rb:shrink-0" />
                      <span className="rb:text-[12px] rb:text-[#5B6167]">{err.message}</span>
                    </Flex>
                  ))}
                </Flex>
              </div>
            ))}
          </Flex>
      }
    </RbDrawer>
  );
});

export default CheckListDrawer;