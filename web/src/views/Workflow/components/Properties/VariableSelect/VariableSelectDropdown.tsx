import { type FC } from 'react';
import { createPortal } from 'react-dom';
import { Flex, Space, Checkbox } from 'antd';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

import type { VariableSelectState } from './useVariableSelect';

interface VariableSelectDropdownProps {
  state: VariableSelectState;
  value?: string | string[];
  multiple: boolean;
}

const VariableSelectDropdown: FC<VariableSelectDropdownProps> = ({ state, value, multiple }) => {
  const { t } = useTranslation();
  const {
    dropdownRef,
    dropdownPos,
    filteredGroups,
    flatItems,
    activeIndex,
    expandedParent,
    selectedValues,
    itemRefs,
    calcChildPos,
    setExpandedPath,
    setExpandedParentKey,
    handleSelect,
  } = state;

  return createPortal(
    <div
      ref={dropdownRef}
      className="rb:w-70 rb:h-57.5 rb:fixed rb:z-1000 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2"
      style={{ top: dropdownPos.top, left: dropdownPos.left, minWidth: dropdownPos.width }}
    >
      <div className="rb:w-full rb:h-54.5 rb:overflow-y-auto">
        {Object.entries(filteredGroups).map(([nodeId, suggestions], index) => {
          const nd = suggestions[0].nodeData;
          return (
            <div key={nodeId} className={clsx("rb:text-[12px]", {
              'rb:mt-3': index !== 0
            })}>
              <div className="rb:px-2 rb:leading-4.25 rb:mb-1.25 rb:font-medium rb:text-[#5B6167]">
                {nd.name}
              </div>
              {suggestions.map(s => {
                const isSelected = multiple
                  ? selectedValues.includes(`{{${s.value}}}`)
                  : `{{${s.value}}}` === value;
                const isExpanded = expandedParent?.key === s.key;
                const hasChildren = !!s.children?.length;
                return (
                  <Flex
                    key={s.key}
                    ref={(el) => { if (el) itemRefs.current.set(s.key, el); }}
                    className={clsx("rb:px-2! rb:py-0.75! rb:rounded-sm rb:leading-4.5 rb:text-[#5B6167] rb:hover:bg-[#F6F6F6]", {
                      'rb:bg-[#F6F6F6]': isSelected || isExpanded || flatItems.indexOf(s) === activeIndex,
                      'rb:cursor-not-allowed rb:opacity-65': s.disabled,
                      'rb:cursor-pointer': !s.disabled,
                    })}
                    align="center"
                    justify="space-between"
                    onClick={() => {
                      if (s.disabled) return;
                      if (hasChildren) {
                        calcChildPos(s.key, true);
                        setExpandedPath([s]);
                        setExpandedParentKey(s.key);
                      }
                      handleSelect(s);
                    }}
                    onMouseEnter={() => {
                      if (hasChildren) {
                        calcChildPos(s.key, true);
                        setExpandedPath([s]);
                        setExpandedParentKey(s.key);
                      } else {
                        setExpandedPath([]);
                        setExpandedParentKey(null);
                      }
                    }}
                  >
                    <div className="rb:font-medium rb:flex-1 rb:break-all">
                      {multiple && (
                        <Checkbox checked={isSelected} className="rb:mr-2!" />
                      )}
                      <span className="rb:text-[#155EEF]">{`{x}`}</span> {s.label}
                    </div>

                    <Space size={2} className="rb:shrink-0">
                      {s.dataType && <span>{s.dataType}</span>}
                      {hasChildren && <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:rotate-90"></div>}
                    </Space>
                  </Flex>
                );
              })}
            </div>
          );
        })}
        {Object.keys(filteredGroups).length === 0 && (
          <div className="rb:px-3 rb:py-4 rb:text-center rb:text-[#bfbfbf] rb:text-[14px]">
            {t('workflow.variableSelect.empty')}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
};

export default VariableSelectDropdown;
