import { type FC, type ReactNode } from 'react';
import { Flex } from 'antd';

interface TablePageLayoutProps {
  title: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
}

/**
 * Standard table page container with white background, title and action area.
 * Used by management pages like MemberManagement, UserManagement, etc.
 */
const TablePageLayout: FC<TablePageLayoutProps> = ({ title, extra, children }) => {
  return (
    <div className="rb:h-full rb:overflow-hidden rb:bg-white rb:rounded-lg rb:pt-3 rb:px-3">
      <Flex justify="space-between" align="center" className="rb:px-1! rb:mb-3!">
        <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[#212332] rb:leading-5">{title}</div>
        {extra && <div>{extra}</div>}
      </Flex>
      {children}
    </div>
  );
};

export default TablePageLayout;
