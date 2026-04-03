/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:40:13 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-03 20:19:34
 */
import { useState, useRef, useEffect, useLayoutEffect, type FC } from 'react'
import { createPortal } from 'react-dom'
import clsx from 'clsx';
import { Flex, Space, Checkbox } from 'antd'
import { useTranslation } from 'react-i18next';

import type { Suggestion } from '../Editor/plugin/AutocompletePlugin'

interface VariableSelectProps {
  options: Suggestion[];
  value?: string | string[];
  allowClear?: boolean;
  filterBooleanType?: boolean;
  multiple?: boolean;
  size?: 'small' | 'middle' | 'large';
  placeholder?: string;
  variant?: 'outlined' | 'borderless';
  className?: string;
  onChange?: (value: string | string[], option: Suggestion | Suggestion[] | undefined) => void;
}

const VariableSelect: FC<VariableSelectProps> = ({
  placeholder,
  options,
  value,
  allowClear = true,
  onChange,
  size = 'middle',
  filterBooleanType = false,
  multiple = false,
  variant = 'outlined',
  className,
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [expandedParent, setExpandedParent] = useState<Suggestion | null>(null);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0, width: 0 });
  const [childPanelPos, setChildPanelPos] = useState({ top: 0, right: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());

  const CHILD_PANEL_HEIGHT = 280; // max-h-60 (240) + header (~40)

  // Calculate dropdown position (runs synchronously after DOM paint to avoid flicker)
  useLayoutEffect(() => {
    if (!open || !containerRef.current) return;
    const triggerRect = containerRef.current.getBoundingClientRect();
    const MARGIN = 8;
    const width = triggerRect.width;
    // Set initial width/left immediately; top will be refined once dropdownRef is available
    if (!dropdownRef.current) {
      setDropdownPos({ top: triggerRect.bottom + MARGIN, left: triggerRect.left, width });
      return;
    }
    const dropdownHeight = dropdownRef.current.offsetHeight;
    const dropdownWidth = dropdownRef.current.offsetWidth;
    const left = Math.min(triggerRect.left, window.innerWidth - dropdownWidth - 10);
    const spaceBelow = window.innerHeight - triggerRect.bottom - MARGIN;
    const spaceAbove = triggerRect.top - MARGIN;
    const top = (spaceBelow >= dropdownHeight || spaceBelow >= spaceAbove)
      ? triggerRect.bottom + MARGIN
      : Math.max(MARGIN, triggerRect.top - dropdownHeight - MARGIN);
    setDropdownPos({ top, left, width });
  }, [open, search, Array.isArray(value) ? value.length : 0]);

  const filteredOptions = filterBooleanType
    ? options.filter(o => o.dataType !== 'boolean')
    : options;

  const allSuggestions = filteredOptions.flatMap(o => o.children ? [o, ...o.children] : [o]);
  const suggestionMap = new Map(allSuggestions.map(s => [`{{${s.value}}}`, s]));

  const selectedValues = multiple ? (Array.isArray(value) ? value : []) : [];
  const selectedSuggestion = !multiple && value ? suggestionMap.get(value as string) : undefined;
  const parentOfSelected = !multiple && value
    ? filteredOptions.find(o => o.children?.some(c => `{{${c.value}}}` === value))
    : undefined;

  const groupedSuggestions = filteredOptions.reduce((groups: Record<string, Suggestion[]>, s) => {
    const nodeId = s.nodeData.id as string;
    if (!groups[nodeId]) groups[nodeId] = [];
    groups[nodeId].push(s);
    return groups;
  }, {});

  const filteredGroups = search
    ? Object.entries(groupedSuggestions).reduce((acc: Record<string, Suggestion[]>, [nodeId, suggestions]) => {
      const matched = suggestions.filter(s =>
        s.label.toLowerCase().includes(search.toLowerCase()) ||
        s.value.toLowerCase().includes(search.toLowerCase()) ||
        s.children?.some(c => c.label.toLowerCase().includes(search.toLowerCase()))
      );
      if (matched.length) acc[nodeId] = matched;
      return acc;
    }, {})
    : groupedSuggestions;

  useEffect(() => {
    if (!open) return;
    const updatePos = () => {
      if (!containerRef.current || !dropdownRef.current) return;
      const triggerRect = containerRef.current.getBoundingClientRect();
      const dropdownHeight = dropdownRef.current.offsetHeight;
      const dropdownWidth = dropdownRef.current.offsetWidth;
      const MARGIN = 8;
      const left = Math.min(triggerRect.left, window.innerWidth - dropdownWidth - 10);
      const spaceBelow = window.innerHeight - triggerRect.bottom - MARGIN;
      const spaceAbove = triggerRect.top - MARGIN;
      let top: number;
      if (spaceBelow >= dropdownHeight || spaceBelow >= spaceAbove) {
        top = triggerRect.bottom + MARGIN;
      } else {
        top = triggerRect.top - dropdownHeight - MARGIN;
        if (top < MARGIN) top = MARGIN;
      }
      setDropdownPos(prev => ({ ...prev, top, left }));
    };
    document.addEventListener('scroll', updatePos, true);
    return () => document.removeEventListener('scroll', updatePos, true);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      const childPanel = document.getElementById('variable-select-child-panel');
      if (
        !containerRef.current?.contains(target) &&
        !dropdownRef.current?.contains(target) &&
        !childPanel?.contains(target)
      ) {
        setOpen(false);
        setSearch('');
        setExpandedParent(null);
        setChildPanelPos({ top: 0, right: 0 });
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const handleSelect = (suggestion: Suggestion) => {
    if (multiple) {
      const key = `{{${suggestion.value}}}`;
      const next = selectedValues.includes(key)
        ? selectedValues.filter(v => v !== key)
        : [...selectedValues, key];
      const nextOptions = next.map(v => suggestionMap.get(v)).filter(Boolean) as Suggestion[];
      onChange?.(next, nextOptions);
    } else {
      onChange?.(`{{${suggestion.value}}}`, suggestion);
      setOpen(false);
      setSearch('');
      setExpandedParent(null);
    }
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    onChange?.(multiple ? [] : '', multiple ? [] : undefined);
  };

  const updateChildPos = (key: string) => {
    const el = itemRefs.current.get(key);
    if (el) {
      const rect = el.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.top - 10;
      const top = spaceBelow >= CHILD_PANEL_HEIGHT
        ? rect.top
        : Math.max(10, window.innerHeight - CHILD_PANEL_HEIGHT - 10);
      setChildPanelPos({ top, right: window.innerWidth - rect.left + 8 });
    }
  };

  const sep = <span className="rb:text-[#DFE4ED] rb:mx-0.5">/</span>;
  const isConversation = (parentOfSelected ?? selectedSuggestion)?.group === 'CONVERSATION' ||
    (selectedSuggestion ? filteredOptions.some(o => o.group === 'CONVERSATION' && o.children?.some(c => `{{${c.value}}}` === value)) : false);
  const nodeData = (parentOfSelected ?? selectedSuggestion)?.nodeData;

  return (
    <div ref={containerRef} className="rb:relative rb:w-full">
      {/* Trigger */}
      <div
        className={clsx(
          'rb:w-full rb:flex rb:items-center rb:justify-between rb:cursor-pointer rb:rounded-md rb:bg-white rb:px-2 rb:transition-colors',
          variant === 'outlined' && 'rb:border rb:border-[#d9d9d9] hover:rb:border-[#4096ff]',
          variant === 'outlined' && open && 'rb:border-[#4096ff] rb:shadow-[0_0_0_2px_rgba(5,145,255,0.1)]',
          variant === 'borderless' && 'rb:border-none rb:shadow-none rb:bg-transparent',
          multiple && size === 'small' ? 'rb:min-h-6 rb:py-0.75' : multiple ? 'rb:min-h-8 rb:py-1' : size === 'small' ? 'rb:h-6 rb:text-[10px]' : size === 'large' ? 'rb:h-10' : 'rb:h-8 rb:text-[12px]',
          !multiple && (size === 'small' ? 'rb:text-[10px]' : 'rb:text-[12px]'),
          className
        )}
        onClick={() => setOpen(o => !o)}
      >
        {multiple ? (
          selectedValues.length > 0 ? (
            <span className="rb:flex rb:flex-wrap rb:gap-1 rb:flex-1 rb:min-w-0">
              {selectedValues.map(v => {
                const s = suggestionMap.get(v);
                if (!s) return null;
                const parent = filteredOptions.find(o => o.children?.some(c => `{{${c.value}}}` === v));
                const nd = s.nodeData;
                const isConv = (parent ?? s)?.group === 'CONVERSATION' ||
                  filteredOptions.some(o => o.group === 'CONVERSATION' && o.children?.some(c => `{{${c.value}}}` === v));
                return (
                  <span
                    key={v}
                    className="rb:inline-flex rb:items-center rb:gap-0.5 rb:bg-[#f0f8ff] rb:rounded rb:px-1 rb:py-0.5 rb:text-[11px] rb:max-w-full"
                  >
                    {!isConv && nd?.icon && <div className={`rb:size-3 rb:shrink-0 rb:bg-cover ${nd.icon}`} />}
                    {!isConv && nd?.name && <span className="rb:text-[#5B6167]">{nd.name}{sep}</span>}
                    <span className="rb:text-[#171719]">
                      {parent ? <>{parent.label}{sep}{s.label}</> : s.label}
                    </span>
                    <span
                      className="rb:cursor-pointer rb:text-[#bfbfbf] hover:rb:text-[#999] rb:leading-none rb:ml-0.5"
                      onClick={(e) => { e.stopPropagation(); handleSelect(s); }}
                    >✕</span>
                  </span>
                );
              })}
            </span>
          ) : (
            <span className="rb:text-[#bfbfbf] rb:flex-1 rb:text-[12px]">{placeholder}</span>
          )
        ) : selectedSuggestion ? (
          <span className="rb:flex rb:flex-1 rb:min-w-0">
            <span className="rb:inline-flex rb:items-center rb:gap-0.5 rb:bg-[#f0f8ff] rb:rounded rb:px-1 rb:py-0.5 rb:text-[11px] rb:max-w-full">
              {!isConversation && nodeData?.icon && <div className={`rb:size-3 rb:shrink-0 rb:bg-cover ${nodeData.icon}`} />}
              {!isConversation && nodeData?.name && <span className="rb:text-[#5B6167]">{nodeData.name}{sep}</span>}
              <span className="rb:text-[#171719]">
                {parentOfSelected ? <>{parentOfSelected.label}{sep}{selectedSuggestion.label}</> : selectedSuggestion.label}
              </span>
            </span>
          </span>
        ) : (
          <span className="rb:text-[#bfbfbf] rb:flex-1">{placeholder}</span>
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
      </div>

      {/* Dropdown via portal */}
      {open && createPortal(
        <div
          ref={dropdownRef}
          className="rb:fixed rb:z-9999 rb:bg-white rb:text-[14px] rb:rounded-xl rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]"
          style={{ top: dropdownPos.top, left: dropdownPos.left, minWidth: dropdownPos.width }}
        >
          <div className="rb:min-w-70 rb:max-h-60 rb:overflow-y-auto rb:py-1">
            {Object.entries(filteredGroups).map(([nodeId, suggestions]) => {
              const nd = suggestions[0].nodeData;
              return (
                <div key={nodeId}>
                  <Flex align="center" gap={4} className="rb:px-3! rb:py-1.25! rb:text-[12px] rb:font-medium rb:text-[#5B6167]">
                    {nd.icon && <div className={`rb:size-3 rb:bg-cover ${nd.icon}`} />}
                    {nd.name}
                  </Flex>
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
                        className="rb:mx-3! rb:pl-3! rb:pr-3! rb:py-1.5! rb:rounded-lg!"
                        align="center"
                        justify="space-between"
                        style={{
                          cursor: s.disabled ? 'not-allowed' : 'pointer',
                          background: isSelected || isExpanded ? '#f0f8ff' : 'white',
                          opacity: s.disabled ? 0.5 : 1,
                        }}
                        onClick={() => {
                          if (s.disabled) return;
                          if (hasChildren) {
                            updateChildPos(s.key);
                            setExpandedParent(prev => prev?.key === s.key ? null : s);
                          }
                          handleSelect(s);
                        }}
                        onMouseEnter={() => {
                          if (hasChildren) {
                            updateChildPos(s.key);
                            setExpandedParent(s);
                          } else {
                            setExpandedParent(null);
                          }
                        }}
                      >
                        <Space size={4}>
                          {multiple && (
                            <Checkbox checked={isSelected} />
                          )}
                          <span className="rb:text-[#155EEF]">{`{x}`}</span>
                          <span>{s.label}</span>
                        </Space>
                        <Space size={4} className="rb:text-[#5B6167] rb:text-[12px]">
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
                {t('workflow.variableSelect.empty', '暂无变量')}
              </div>
            )}
          </div>
        </div>,
        document.body
      )}

      {/* Child panel via portal — escapes overflow clipping */}
      {open && expandedParent?.children?.length && createPortal(
        <div
          id="variable-select-child-panel"
          className="rb:fixed rb:z-9999 rb:bg-white rb:rounded-xl rb:py-1 rb:min-w-60 rb:max-h-60 rb:overflow-y-auto rb:text-[14px] rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]"
          style={{ top: childPanelPos.top, right: childPanelPos.right }}
          onMouseEnter={() => setExpandedParent(expandedParent)}
        >
          <div
            className="rb:px-3 rb:py-2 rb:text-[14px] rb:font-medium rb:text-[#5B6167] rb:border-b rb:border-[#F0F0F0] rb:cursor-pointer rb:hover:bg-[#f0f8ff]"
            onClick={() => !expandedParent.disabled && handleSelect(expandedParent)}
          >
            <Flex justify="space-between" align="center" gap={8}>
              <Flex align="center" gap={6}>
                <span>{expandedParent.nodeData.name}.{expandedParent.label}</span>
              </Flex>
              <span>{expandedParent.dataType}</span>
            </Flex>
          </div>
          {expandedParent.children.map(child => {
            const isSelected = multiple
              ? selectedValues.includes(`{{${child.value}}}`)
              : `{{${child.value}}}` === value;
            const hasGrandChildren = !!child.children?.length;
            return (
              <Flex
                key={child.key}
                className={clsx("rb:px-3! rb:py-2! rb:hover:bg-[#f0f8ff]!", {
                  'rb:bg-[#f0f8ff]': isSelected,
                  'rb:white': !isSelected
                })}
                align="center"
                justify="space-between"
                style={{
                  cursor: child.disabled ? 'not-allowed' : 'pointer',
                  opacity: child.disabled ? 0.5 : 1,
                }}
                onClick={() => !child.disabled && handleSelect(child)}
              >
                <Flex align="center" gap={6}>
                  {multiple && (
                    <Checkbox checked={isSelected} />
                  )}
                  <span>{child.label}</span>
                </Flex>
                <Flex align="center" gap={4}>
                  {child.dataType && <span className="rb:text-[#5B6167]">{child.dataType}</span>}
                  {hasGrandChildren && <span className="rb:text-[#5B6167] rb:ml-1">›</span>}
                </Flex>
              </Flex>
            );
          })}
        </div>,
        document.body
      )}
    </div>
  );
};

export default VariableSelect
