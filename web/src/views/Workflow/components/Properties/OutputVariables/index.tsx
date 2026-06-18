/*
 * @Author: ZhaoYing
 * @Date: 2026-06-11 17:55:00
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-11 18:04:04
 */
import { type FC, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { Flex, Tree } from 'antd';

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'

interface OutputVariablesProps {
  /** Current node output variables */
  variables: Suggestion[];
}

interface TreeNode {
  title: React.ReactNode;
  key: string;
  children?: TreeNode[];
}

/**
 * OutputVariables component
 * Displays the output variables of the currently selected node as a collapsible tree
 * @param props - Component props
 */
const OutputVariables: FC<OutputVariablesProps> = ({ variables }) => {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(true);

  /**
   * Recursively build tree node from variable item, recursing all the way down
   * until no children exist
   * @param item - Variable item
   * @returns Tree node
   */
  const buildTreeNode = (item: Suggestion): TreeNode => {
    const children = item.children?.length
      ? item.children.map(buildTreeNode)
      : undefined;
    return {
      title: (
        <Flex gap={4}>
          <span className="rb:font-medium">{item.label}</span>
          <span className="rb:text-[#212332]"> {item.dataType}</span>
        </Flex>
      ),
      key: item.value,
      children,
    };
  };

  /** Tree data, memoized to avoid recomputing on every render */
  const treeData = useMemo(() => variables.map(buildTreeNode), [variables]);

  /**
   * Toggle collapsed state
   */
  const handleToggle = () => {
    setCollapsed((prev) => !prev);
  };

  return (
    <div className="rb:text-[12px] rb:leading-4.5">
      <Flex gap={8} vertical>
        <Flex align="center" className="rb:font-medium rb:cursor-pointer" onClick={handleToggle}>
          {t('workflow.config.outputVariable')}
          <div
            className={clsx(
              "rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/caret_right_outlined.svg')]",
              { 'rb:rotate-90': !collapsed }
            )}
          ></div>
        </Flex>
        {!collapsed && (
          <Tree
            treeData={treeData}
            defaultExpandAll
            className="rb:text-[12px]!"
            selectable={false}
          />
        )}
      </Flex>
    </div>
  );
};

export default OutputVariables;
