/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:40:13 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-03 18:52:25
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
  variant?: 'outlined' | 'borderless' | 'filled';
  className?: string;
  onChange?: (value?: string | string[], option?: Suggestion | Suggestion[] | undefined) => void;
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
  const [expandedParentKey, setExpandedParentKey] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState<number>(-1);
  const [activePanel, setActivePanel] = useState<'main' | 'child'>('main');
  const [childActiveIndex, setChildActiveIndex] = useState<number>(-1);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0, width: 0 });
  const [childPanelPos, setChildPanelPos] = useState({ top: 0, right: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const childItemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const activeKeyRef = useRef<string | null>(null);

  const CHILD_PANEL_HEIGHT = 280; // max-h-60 (240) + header (~40)

  const calcChildPos = (key: string) => {
    const el = itemRefs.current.get(key);
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const dropdownEl = dropdownRef.current;
    if (!dropdownEl) return;
    const dropdownRect = dropdownEl.getBoundingClientRect();
    const dropdownBottom = dropdownRect.bottom;
    const actualChildHeight = Math.min(CHILD_PANEL_HEIGHT, dropdownRect.height);
    // Bottom-align child panel with main panel
    const top = Math.max(10, dropdownBottom - actualChildHeight);
    setChildPanelPos({ top, right: window.innerWidth - rect.left + 8 });
  };

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
    // Re-calculate child panel position if expanded
    if (expandedParentKey) calcChildPos(expandedParentKey);
  }, [open, search, Array.isArray(value) ? value.length : 0, options.length, expandedParentKey]);

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

  const expandedParent = expandedParentKey
    ? filteredOptions.find(o => o.key === expandedParentKey) ?? null
    : null;

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
    if (!expandedParentKey) return;
    calcChildPos(expandedParentKey);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dropdownPos, expandedParentKey]);

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
        setExpandedParentKey(null);
        setChildPanelPos({ top: 0, right: 0 });
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // Flat list of all visible selectable items (main panel only, no children expanded inline)
  const flatItems = Object.values(filteredGroups).flat();

  useEffect(() => {
    setActiveIndex(-1);
    setActivePanel('main');
    setChildActiveIndex(-1);
  }, [open, search]);

  useEffect(() => {
    if (activeIndex < 0 || activeIndex >= flatItems.length) {
      setExpandedParentKey(null);
      return;
    }
    const s = flatItems[activeIndex];
    activeKeyRef.current = s.key;
    itemRefs.current.get(s.key)?.scrollIntoView({ block: 'nearest' });
    if (s.children?.length) {
      calcChildPos(s.key);
      setExpandedParentKey(s.key);
    } else {
      setExpandedParentKey(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIndex]);

  useEffect(() => {
    if (!expandedParent?.children?.length || childActiveIndex < 0) return;
    const child = expandedParent.children[childActiveIndex];
    if (child) childItemRefs.current.get(child.key)?.scrollIntoView({ block: 'nearest' });
  }, [childActiveIndex, expandedParent]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      const children = expandedParent?.children ?? [];
      if (activePanel === 'child') {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setChildActiveIndex(i => Math.min(i + 1, children.length - 1));
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          setChildActiveIndex(i => Math.max(i - 1, 0));
        } else if (e.key === 'ArrowRight') {
          e.preventDefault();
          setActivePanel('main');
          setChildActiveIndex(-1);
        } else if (e.key === 'Enter' && childActiveIndex >= 0 && childActiveIndex < children.length) {
          e.preventDefault();
          const child = children[childActiveIndex];
          if (!child.disabled) handleSelect(child);
        } else if (e.key === 'Escape') {
          setOpen(false);
        }
      } else {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setActiveIndex(i => Math.min(i + 1, flatItems.length - 1));
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          setActiveIndex(i => Math.max(i - 1, 0));
        } else if (e.key === 'ArrowLeft') {
          e.preventDefault();
          if (expandedParent?.children?.length) {
            setActivePanel('child');
            setChildActiveIndex(0);
          }
        } else if (e.key === 'Enter' && activeIndex >= 0 && activeIndex < flatItems.length) {
          e.preventDefault();
          const s = flatItems[activeIndex];
          if (!s.disabled) handleSelect(s);
        } else if (e.key === 'Escape') {
          setOpen(false);
        }
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeIndex, activePanel, childActiveIndex, flatItems, expandedParent]);

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
      setExpandedParentKey(null);
    }
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    onChange?.(multiple ? [] : undefined, multiple ? [] : undefined);
  };
  const sep = <span className="rb:text-[#DFE4ED] rb:mx-0.5">/</span>;
  const isConversation = (parentOfSelected ?? selectedSuggestion)?.group === 'CONVERSATION' || 
    (parentOfSelected ?? selectedSuggestion)?.group === 'SYSTEM' || 
    (parentOfSelected ?? selectedSuggestion)?.group === 'ENV'
    || (selectedSuggestion?.group === 'CONVERSATION' && selectedSuggestion?.children?.some(c => `{{${c.value}}}` === value))
    || (selectedSuggestion?.group === 'SYSTEM' && selectedSuggestion?.children?.some(c => `{{${c.value}}}` === value))
    || (selectedSuggestion?.group === 'ENV' && selectedSuggestion?.children?.some(c => `{{${c.value}}}` === value))
    || (selectedSuggestion ? filteredOptions.some(o => o.group === 'CONVERSATION' && o.children?.some(c => `{{${c.value}}}` === value)) : false);
  const nodeData = (parentOfSelected ?? selectedSuggestion)?.nodeData;

  return (
    <div ref={containerRef} className={`rb:relative rb:w-full ${className}`}>
      {/* Trigger */}
      <div
        className={clsx(
          'rb:w-full rb:flex rb:items-center rb:justify-between rb:cursor-pointer rb:rounded-lg rb:px-2 rb:transition-colors', {
            'rb:bg-[#F6F6F6] rb:border-none rb:shadow-none': variant === 'filled',
            'rb:border rb:border-[#d9d9d9] hover:rb:border-[#4096ff] rb:bg-white': variant === 'outlined',
            'rb:border-[#171719]!': variant === 'outlined' && open,
            'rb:border-none rb:shadow-none rb:bg-transparent': variant === 'borderless',
            'rb:text-[12px]': size === 'small',
            'rb:text-[14px]': size !== 'small',
          },
          multiple && size === 'small'
            ? 'rb:min-h-7 rb:py-0.75'
            : multiple
            ? 'rb:min-h-8 rb:py-1'
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
                const parent = filteredOptions.find(o => o.children?.some(c => `{{${c.value}}}` === v));
                const nd = s.nodeData;
                const isConv = (parent ?? s)?.group === 'CONVERSATION' ||
                  filteredOptions.some(o => o.group === 'CONVERSATION' && o.children?.some(c => `{{${c.value}}}` === v));
                return (
                  <span
                    key={v}
                    className="rb-border rb:rounded-md rb:bg-white rb:text-[10px] rb:text-[#212332] rb:h-5! rb:inline-flex rb:items-center rb:p-1 rb:cursor-pointer"
                  >
                    {!isConv && nd?.icon && <div className={`rb:size-3 rb:bg-cover ${nd.icon}`} />}
                    {!isConv && nd?.name && <span className="rb:text-[#5B6167]">{nd.name}{sep}</span>}
                    <span>
                      {parent ? <>{parent.label}{sep}{s.label}</> : s.label}
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
          <div className="rb:flex rb:flex-1 rb:min-w-0 rb:max-w-full">
            <span
              className="rb-border rb:rounded-md rb:bg-white rb:text-[10px] rb:text-[#212332] rb:h-5! rb:inline-flex rb:items-center rb:p-1 rb:cursor-pointer"
            >
              {!isConversation && nodeData?.icon && <div className={`rb:size-3 rb:bg-cover rb:mr-1 ${nodeData.icon}`} />}
              {!isConversation && nodeData?.name && <span className="rb:shrink rb:min-w-0 rb:truncate rb:max-w-[40%]">{nodeData.name}</span>}
              {!isConversation && nodeData?.name && <span>{sep}</span>}
              <span className="rb:shrink rb:min-w-0 rb:truncate">
                {parentOfSelected ? <>{parentOfSelected.label}{sep}{selectedSuggestion.label}</> : selectedSuggestion.label}
              </span>
            </span>
          </div>
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
      </div>

      {/* Dropdown via portal */}
      {open && createPortal(
        <div
          ref={dropdownRef}
          className="rb:min-w-70 rb:max-h-57.5 rb:overflow-y-auto rb:fixed rb:z-1000 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2"
          style={{ top: dropdownPos.top, left: dropdownPos.left, minWidth: dropdownPos.width }}
        >
          <div className="rb:min-w-70 rb:max-h-57.5 rb:overflow-y-auto">
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
                            calcChildPos(s.key);
                            setExpandedParentKey(prev => prev === s.key ? null : s.key);
                          }
                          handleSelect(s);
                        }}
                        onMouseEnter={() => {
                          if (hasChildren) {
                            calcChildPos(s.key);
                            setExpandedParentKey(s.key);
                          } else {
                            setExpandedParentKey(null);
                          }
                        }}
                      >
                        <div className="rb:font-medium">
                          {multiple && (
                            <Checkbox checked={isSelected} className="rb:mr-2!" />
                          )}
                          <span className="rb:text-[#155EEF]">{`{x}`}</span> {s.label}
                        </div>

                        <Space size={2}>
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
      )}

      {/* Child panel via portal — escapes overflow clipping */}
      {open && expandedParent?.children?.length && createPortal(
        <div
          id="variable-select-child-panel"
          className="rb:min-w-70 rb:max-h-57.5 rb:overflow-y-auto rb:text-[12px] rb:fixed rb:z-1000 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2"
          style={{ top: childPanelPos.top, right: childPanelPos.right }}
          onMouseEnter={() => setExpandedParentKey(expandedParentKey)}
        >
          <div className="rb:pb-2 rb:mb-1 rb:font-medium rb:text-[#5B6167] rb-border-b">
            <Flex justify="space-between" align="center" gap={8}>
              <span>{expandedParent.nodeData.name}.{expandedParent.label}</span>
              <span>{expandedParent.dataType}</span>
            </Flex>
          </div>
          {expandedParent.children.map((child, ci) => {
            const isSelected = multiple
              ? selectedValues.includes(`{{${child.value}}}`)
              : `{{${child.value}}}` === value;
            const isChildActive = activePanel === 'child' && ci === childActiveIndex;
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
                onClick={() => !child.disabled && handleSelect(child)}
              >
                <Flex align="center" gap={8}>
                  {multiple && (
                    <Checkbox checked={isSelected} />
                  )}
                  <span className="rb:font-medium">{child.label}</span>
                </Flex>
                <Space size={2}>
                  {child.dataType && <span>{child.dataType}</span>}
                </Space>
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
