/**
 * Detail-page prototype: the preview section only appears when there are
 * failures, and it lists failed entries only.
 */
import { useState } from 'react';
import { Button, Flex, Space } from 'antd';

import useFaqImportI18n from './hooks/useFaqImportI18n';
import type { FaqImportIssue, FaqImportParseResult } from './faqImportFile';
import RbAlert from '@/components/RbAlert'

interface FaqImportPreviewProps {
  result: FaqImportParseResult;
}

const PAGE_SIZE = 5;

export default function FaqImportPreview({ result }: FaqImportPreviewProps) {
  const key = useFaqImportI18n();
  const [page, setPage] = useState(1);
  const formatIssue = (item: FaqImportIssue) => key(`errors.${item.code}`, item.params);

  if (result.error) {
    return <RbAlert color="red" className="rb:mt-4!">{formatIssue(result.error)}</RbAlert>;
  }

  const failures = result.rows
    .map((row, index) => ({ row, index: index + 1 }))
    .filter(({ row }) => row.errors.length > 0);
  if (!failures.length) return null;

  const pages = Math.max(1, Math.ceil(failures.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages);
  const pageRows = failures.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const positionLabel = (line: number) => key(result.isJson ? 'positionItem' : 'positionLine', { line });

  return (
    <Flex vertical gap={12} className="rb:mt-4!">
      <Flex justify="space-between" align="center" gap={12} wrap>
        <span className="rb:font-medium rb:text-gray-800">{key('previewTitle', { count: result.rows.length })}</span>
        <span className="rb:text-[12px] rb:text-red-500">{key('invalidCount', { count: failures.length })}</span>
      </Flex>
      <div className="rb:text-gray-600 rb:text-[12px]">{key('invalidHint')}</div>
      <ul className="rb:list-none rb:p-0! rb:m-0! rb:overflow-hidden">
        {pageRows.map(({ row, index }) => (
          <li key={row.source_position} className="rb:border-b rb:border-[#eaecee] rb:bg-[#fff7f5] rb:p-3!">
            <Flex align="center" gap={10} className="rb:min-h-6 rb:min-w-0">
              <span className="rb:grid rb:shrink-0 rb:size-[22px] rb:place-items-center rb:rounded rb:bg-[#fce7e4] rb:text-[#b63835] rb:text-[12px] rb:leading-none">
                {index}
              </span>
              <span className="rb:min-w-0 rb:flex-1 rb:text-[13px] rb:[overflow-wrap:anywhere]">{row.standard_question || key('emptyQuestion')}</span>
            </Flex>
            <div className="rb:mt-1.5! rb:ml-8! rb:text-[12px] rb:leading-[1.7] rb:text-[#b63835] rb:[overflow-wrap:anywhere]">
              {positionLabel(row.source_position)}: {row.errors.map(formatIssue).join('; ')}
            </div>
          </li>
        ))}
      </ul>
      <Flex justify="space-between" align="center" gap={12} wrap>
        <span className="rb:text-gray-600">{key('pagination', { page: currentPage, pages, count: failures.length })}</span>
        <Space size={8}>
          <Button disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>{key('prev')}</Button>
          <Button disabled={currentPage === pages} onClick={() => setPage(currentPage + 1)}>{key('next')}</Button>
        </Space>
      </Flex>
    </Flex>
  );
}
