/**
 * Detail-page prototype: compact progress panel shown above the FAQ table
 * while an import task is running.
 */
import { Flex } from 'antd';

import useFaqImportI18n from './hooks/useFaqImportI18n';
import type { FaqImportTaskVo } from './types';

interface FaqImportProgressProps {
  task: FaqImportTaskVo | null;
  visible: boolean;
}

export default function FaqImportProgress({ task, visible }: FaqImportProgressProps) {
  const key = useFaqImportI18n();
  if (!visible) return null;

  const total = task?.progress.total_count ?? 0;
  const processed = task?.progress.processed_count ?? 0;
  const percent = total ? Math.round((processed / total) * 100) : 0;

  return (
    <Flex vertical gap={8} className="rb:mb-4! rb:rounded-lg rb:border rb:border-[#dfe8fa] rb:bg-[#f8faff] rb:px-4! rb:py-3!">
      <Flex align="center" gap={10} className="rb:text-[12px] rb:leading-5">
        <span className="rb:font-medium rb:text-gray-800">{key('progressTitle')}</span>
        <span className="rb:text-gray-600">{key('progressCount', { processed, total })}</span>
        <span className="rb:ml-auto rb:font-semibold rb:text-[#155eef]">{percent}%</span>
      </Flex>
      <div className="rb:h-1.25 rb:overflow-hidden rb:rounded rb:bg-[#e2e9f5]">
        <div className="rb:h-full rb:rounded rb:bg-[#155eef] rb:transition-[width]" style={{ width: `${percent}%` }} />
      </div>
    </Flex>
  );
}
