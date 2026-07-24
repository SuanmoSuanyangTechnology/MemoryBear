/*
 * @Description: Document preview pagination control bar
 */
import { Button, InputNumber, Flex } from 'antd';
import { LeftOutlined, RightOutlined } from '@ant-design/icons';
import type { FC, ReactNode } from 'react';

interface PaginationBarProps {
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  extraControls?: ReactNode;
}

const PaginationBar: FC<PaginationBarProps> = ({
  currentPage,
  totalPages,
  onPageChange,
  extraControls,
}) => (
  <Flex align="center" justify="center" gap={12} className="rb:py-2! rb:px-4! rb:bg-white rb:border-t rb:border-gray-200 rb:select-none">
    <Button
      size="small"
      icon={<LeftOutlined />}
      disabled={currentPage <= 1}
      onClick={() => onPageChange(currentPage - 1)}
    />
    <Flex align="center" gap={4} className="rb:text-sm rb:text-gray-600">
      <InputNumber
        size="small"
        min={1}
        max={totalPages}
        value={currentPage}
        onChange={(val) => val && onPageChange(val)}
        style={{ width: 56 }}
      />
      <span>/ {totalPages}</span>
    </Flex>
    <Button
      size="small"
      icon={<RightOutlined />}
      disabled={currentPage >= totalPages}
      onClick={() => onPageChange(currentPage + 1)}
    />
    {extraControls}
  </Flex>
);

export default PaginationBar;
