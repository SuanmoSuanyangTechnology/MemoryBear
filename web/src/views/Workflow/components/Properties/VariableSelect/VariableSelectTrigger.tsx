import { type FC } from 'react';
import { Flex, Space } from 'antd';
import clsx from 'clsx';

import type { Suggestion } from '../../Editor/plugin/autocomplete/types';
import type { VariableSelectState } from './useVariableSelect';

const sep = <span className="rb:text-[#DFE4ED] rb:mx-0.5">/</span>;

interface VariableSelectTriggerProps {
  state: VariableSelectState;
  value?: string | string[];
  multiple: boolean;
  allowClear: boolean;
  size: 'small' | 'middle' | 'large';
  variant: 'outlined' | 'borderless' | 'filled';
  placeholder?: string;
  className?: string;
}

const isConversationGroup = (group?: string) =>
  group === 'CONVERSATION' || group === 'SYSTEM' || group === 'ENV';

const VariableSelectTrigger: FC<VariableSelectTriggerProps> = ({
  state,
  value,
  multiple,
  allowClear,
  size,
  variant,
  placeholder,
  className,
}) => {
  const {
    open, setOpen,
    suggestionMap,
    suggestionParentMap,
    selectedValues,
    selectedSuggestion,
    parentOfSelected,
    filteredOptions,
    handleSelect,
    handleClear,
  } = state;

  const isConversation = isConversationGroup((parentOfSelected ?? selectedSuggestion)?.group)
    || (selectedSuggestion?.group === 'CONVERSATION' && selectedSuggestion?.children?.some(c => `{{${c.value}}}` === value))
    || (selectedSuggestion?.group === 'SYSTEM' && selectedSuggestion?.children?.some(c => `{{${c.value}}}` === value))
    || (selectedSuggestion?.group === 'ENV' && selectedSuggestion?.children?.some(c => `{{${c.value}}}` === value))
    || (selectedSuggestion ? filteredOptions.some(o => o.group === 'CONVERSATION' && o.children?.some(c => `{{${c.value}}}` === value)) : false);
  const nodeData = (parentOfSelected ?? selectedSuggestion)?.nodeData;

  return (
    <Flex
      align="center"
      justify="space-between"
      className={clsx(
        'rb:w-full rb:cursor-pointer rb:rounded-lg rb:px-2! rb:transition-colors', {
          'rb:bg-[#F6F6F6] rb:border-none rb:shadow-none': variant === 'filled',
          'rb:border rb:border-[#d9d9d9] hover:rb:border-[#4096ff] rb:bg-white': variant === 'outlined',
          'rb:border-[#171719]!': variant === 'outlined' && open,
          'rb:border-none rb:shadow-none rb:bg-transparent': variant === 'borderless',
          'rb:text-[12px]': size === 'small',
          'rb:text-[14px]': size !== 'small',
        },
        multiple && size === 'small'
          ? 'rb:min-h-7 rb:py-0.75!'
          : multiple
          ? 'rb:min-h-8 rb:py-1!'
          : size === 'small'
          ? 'rb:h-7 rb:text-[10px]'
          : size === 'large'
          ? 'rb:h-10'
          : 'rb:h-8 rb:text-[12px]',
        className
      )}
      onClick={() => setOpen(o => !o)}
    >
      {multiple ? (
        selectedValues.length > 0 ? (
          <Flex wrap gap={4} className="rb:flex-1! rb:min-w-0">
            {selectedValues.map(v => {
              const s = suggestionMap.get(v);
              if (!s) return null;
              // Walk up the parent chain to find the root (top-level option)
              let root: Suggestion = s;
              let cursor: Suggestion | undefined = suggestionParentMap.get(v);
              while (cursor) {
                root = cursor;
                cursor = suggestionParentMap.get(`{{${cursor.value}}}`);
              }
              // Build breadcrumb path from root to current selection
              const path: Suggestion[] = [];
              let cur: Suggestion | undefined = s;
              while (cur) {
                path.unshift(cur);
                cur = suggestionParentMap.get(`{{${cur.value}}}`);
              }
              const nd = root.nodeData;
              const isConv = isConversationGroup(root.group);
              return (
                <span
                  key={v}
                  className="rb-border rb:rounded-md rb:bg-white rb:text-[10px] rb:text-[#212332] rb:h-5! rb:inline-flex rb:items-center rb:p-1 rb:cursor-pointer rb:max-w-full!"
                >
                  {!isConv && nd?.icon && <div className={`rb:size-3 rb:shrink-0 rb:bg-cover ${nd.icon}`} />}
                  {!isConv && nd?.name && <span className="rb:text-[#5B6167]">{nd.name}{sep}</span>}
                  <span>
                    {path.map((p, idx) => (
                      <span key={p.key}>
                        {idx > 0 && sep}
                        {p.label}
                      </span>
                    ))}
                  </span>
                  <span
                    className="rb:cursor-pointer rb:text-[#bfbfbf] hover:rb:text-[#999] rb:leading-none rb:ml-0.5"
                    onClick={(e) => { e.stopPropagation(); handleSelect(s); }}
                  >✕</span>
                </span>
              );
            })}
          </Flex>
        ) : (
          <span className="rb:text-[rgba(23,23,25,0.25)] rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:flex-1">{placeholder}</span>
        )
      ) : selectedSuggestion ? (
        <Flex className="rb:flex-1 rb:min-w-0 rb:max-w-full">
          <span
            className="rb-border rb:rounded-md rb:bg-white rb:text-[10px] rb:text-[#212332] rb:h-5! rb:inline-flex rb:items-center rb:p-1 rb:cursor-pointer rb:max-w-full!"
          >
            {!isConversation && nodeData?.icon && <div className={`rb:size-3 rb:shrink-0 rb:bg-cover rb:mr-1 ${nodeData.icon}`} />}
            {!isConversation && nodeData?.name && <span className="rb:shrink rb:min-w-0 rb:truncate rb:max-w-[40%]">{nodeData.name}</span>}
            {!isConversation && nodeData?.name && <span>{sep}</span>}
            <span className="rb:shrink rb:min-w-0 rb:truncate">
              {parentOfSelected ? <>{parentOfSelected.label}{sep}{selectedSuggestion.label}</> : selectedSuggestion.label}
            </span>
          </span>
        </Flex>
      ) : (
        <span className="rb:text-[rgba(23,23,25,0.25)] rb:flex-1">{placeholder}</span>
      )}
      <Space size={4} className="rb:shrink-0 rb:ml-1">
        {allowClear && (
          <span
            className={clsx('rb:text-[#bfbfbf] rb:text-[10px] hover:rb:text-[#999] rb:leading-none rb:transition-opacity',
              (multiple ? selectedValues.length > 0 : !!selectedSuggestion) ? 'rb:opacity-100 rb:cursor-pointer' : 'rb:opacity-0 rb:pointer-events-none'
            )}
            onClick={handleClear}
          >✕</span>
        )}
        <div className={clsx("rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')]", {
          'rb:rotate-0': open,
          'rb:rotate-180': !open,
        })}></div>
      </Space>
    </Flex>
  );
};

export default VariableSelectTrigger;
