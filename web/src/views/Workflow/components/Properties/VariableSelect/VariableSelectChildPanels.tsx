import { type FC } from 'react';
import { createPortal } from 'react-dom';
import { Flex, Space, Checkbox } from 'antd';
import clsx from 'clsx';

import type { VariableSelectState } from './useVariableSelect';

interface VariableSelectChildPanelsProps {
  state: VariableSelectState;
  value?: string | string[];
  multiple: boolean;
}

const VariableSelectChildPanels: FC<VariableSelectChildPanelsProps> = ({ state, value, multiple }) => {
  const {
    expandedPath,
    childPanelPos,
    panelPositions,
    activePanel,
    childActiveIndex,
    selectedValues,
    childItemRefs,
    calcChildPos,
    setExpandedPath,
    setExpandedParentKey,
    handleSelect,
  } = state;

  return (
    <>
      {expandedPath.map((parent, index) => {
        const panelKey = parent.key;
        const position = index === 0 ? childPanelPos : panelPositions.get(panelKey);
        if (!position) return null;

        return createPortal(
          <div
            key={panelKey}
            id={`variable-select-child-panel-${panelKey}`}
            className="rb:w-70 rb:h-57.5 rb:overflow-y-auto rb:text-[12px] rb:fixed rb:z-1000 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2"
            style={{
              top: position.top,
              [position.useRight ? 'right' : 'left']: position.horizontal
            }}
            onMouseEnter={() => setExpandedParentKey(panelKey)}
          >
            {/* Breadcrumb header */}
            <div className="rb:pb-2 rb:mb-1 rb:font-medium rb:text-[#5B6167] rb-border-b">
              <Flex justify="space-between" align="center" gap={8}>
                <Flex align="center" gap={2} className="rb:flex-1! rb:break-all">
                  <span>
                    {expandedPath.slice(0, index + 1).map((item, idx) => (
                      <span key={item.key}>
                        {idx > 0 && '.'}
                        {item.label}
                      </span>
                    ))}
                  </span>
                </Flex>
                <span className="rb:shrink-0">{parent.dataType}</span>
              </Flex>
            </div>
            {parent.children?.map((child, ci) => {
              const isSelected = multiple
                ? selectedValues.includes(`{{${child.value}}}`)
                : `{{${child.value}}}` === value;
              const isChildActive = activePanel === 'child' && ci === childActiveIndex;
              const hasChildren = !!child.children?.length;
              return (
                <Flex
                  key={child.key}
                  ref={(el) => { if (el) childItemRefs.current.set(child.key, el); }}
                  className={clsx("rb:px-2! rb:py-0.75! rb:rounded-sm rb:leading-4.5 rb:text-[#5B6167] rb:hover:bg-[#F6F6F6]", {
                    'rb:bg-[#F6F6F6]': isSelected || isChildActive,
                    'rb:cursor-not-allowed rb:opacity-65': child.disabled,
                    'rb:cursor-pointer': !child.disabled,
                  })}
                  align="center"
                  justify="space-between"
                  onClick={() => {
                    if (child.disabled) return;
                    handleSelect(child);
                  }}
                  onMouseEnter={() => {
                    if (hasChildren) {
                      calcChildPos(child.key);
                      setExpandedPath([...expandedPath.slice(0, index + 1), child]);
                      setExpandedParentKey(child.key);
                    } else {
                      // No children: close any deeper level panels
                      setExpandedPath(expandedPath.slice(0, index + 1));
                      setExpandedParentKey(parent.key);
                    }
                  }}
                >
                  <Flex align="center" gap={8} className="rb:flex-1 rb:break-all">
                    {multiple && (
                      <Checkbox checked={isSelected} />
                    )}
                    <span className="rb:font-medium">{child.label}</span>
                  </Flex>
                  <Space size={2} className="rb:shrink-0">
                    {child.dataType && <span>{child.dataType}</span>}
                    {hasChildren && <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:rotate-90"></div>}
                  </Space>
                </Flex>
              );
            })}
          </div>,
          document.body
        );
      })}
    </>
  );
};

export default VariableSelectChildPanels;
