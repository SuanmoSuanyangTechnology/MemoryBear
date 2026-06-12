/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 15:40:13 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-12 12:16:14
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
  const [expandedPath, setExpandedPath] = useState<Suggestion[]>([]);
  const [panelPositions, setPanelPositions] = useState<Map<string, { top: number; horizontal: number; useRight: boolean }>>(new Map());
  const [activeIndex, setActiveIndex] = useState<number>(-1);
  const [activePanel, setActivePanel] = useState<'main' | 'child'>('main');
  const [childActiveIndex, setChildActiveIndex] = useState<number>(-1);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0, width: 0 });
  const [childPanelPos, setChildPanelPos] = useState({ top: 0, horizontal: 0, useRight: true });
  const containerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const childItemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const activeKeyRef = useRef<string | null>(null);

  const CHILD_PANEL_HEIGHT = 280; // max-h-60 (240) + header (~40)
  const CHILD_PANEL_WIDTH = 280; // min-w-70 (280px)
  const MARGIN = 8;

  const calcChildPos = (key: string, fromMainPanel: boolean = false) => {
    // Calculate position that avoids screen edges
    const calculateSmartPos = (rect: DOMRect) => {
      const spaceRight = window.innerWidth - rect.left;

      // Determine horizontal position: prefer right, fallback to left
      const useRight = spaceRight >= CHILD_PANEL_WIDTH + MARGIN;
      const horizontal = useRight
        ? window.innerWidth - rect.left + MARGIN
        : rect.right + MARGIN;

      // Determine vertical position: align panel top with main dropdown top
      // Panel has a fixed height and shares the same vertical edges as the parent dropdown
      let top: number;
      if (dropdownRef.current) {
        const parentRect = dropdownRef.current.getBoundingClientRect();
        top = parentRect.top;
      } else {
        // Fallback: align panel bottom with parent variable bottom
        const calculatedTop = rect.bottom - CHILD_PANEL_HEIGHT;
        top = Math.max(MARGIN, calculatedTop);
      }

      return { top, horizontal, useRight };
    };

    // For panels opened from main panel
    if (fromMainPanel) {
      const el = itemRefs.current.get(key);
      if (!el) return;
      const rect = el.getBoundingClientRect();
      
      const { top, horizontal, useRight } = calculateSmartPos(rect);
      setChildPanelPos({ top, horizontal, useRight });
    } else {
      // For panels opened from child panels - position relative to the parent variable
      const el = childItemRefs.current.get(key);
      if (!el) return;
      const rect = el.getBoundingClientRect();
      
      const { top, horizontal, useRight } = calculateSmartPos(rect);
      const newPos = { top, horizontal, useRight };
      setPanelPositions(prev => new Map(prev).set(key, newPos));
    }
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

  // Build flat map including all nested levels + parent map for breadcrumb lookup
  const { suggestionMap, suggestionParentMap } = filteredOptions.reduce<{
    suggestionMap: Map<string, Suggestion>;
    suggestionParentMap: Map<string, Suggestion>;
  }>((acc, o) => {
    const walk = (s: Suggestion, parent: Suggestion | null) => {
      const key = `{{${s.value}}}`;
      acc.suggestionMap.set(key, s);
      if (parent) acc.suggestionParentMap.set(key, parent);
      s.children?.forEach(c => walk(c, s));
    };
    walk(o, null);
    return acc;
  }, { suggestionMap: new Map(), suggestionParentMap: new Map() });
  console.log('selectedValues value', value)
  const selectedValues = multiple ? (Array.isArray(value) ? value : []) : [];
  const selectedSuggestion = !multiple && value ? suggestionMap.get(value as string) : undefined;
  const parentOfSelected = !multiple && value
    ? filteredOptions.find(o => o.children?.some(c => `{{${c.value}}}` === value))
    : undefined;

  const expandedParent = expandedPath.length > 0
    ? expandedPath[expandedPath.length - 1]
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
      const inChildPanel = Array.from(document.querySelectorAll('[id^="variable-select-child-panel-"]'))
        .some(panel => panel.contains(target));
      if (
        !containerRef.current?.contains(target) &&
        !dropdownRef.current?.contains(target) &&
        !inChildPanel
      ) {
        setOpen(false);
        setSearch('');
        setExpandedParentKey(null);
        setExpandedPath([]);
        setPanelPositions(new Map());
        setChildPanelPos({ top: 0, horizontal: 0, useRight: true });
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
      setExpandedPath([]);
      return;
    }
    const s = flatItems[activeIndex];
    activeKeyRef.current = s.key;
    itemRefs.current.get(s.key)?.scrollIntoView({ block: 'nearest' });
    
    if (s.children?.length) {
      // Delay position calculation to ensure DOM ref is set
      const timer = setTimeout(() => {
        calcChildPos(s.key, true);
        setExpandedParentKey(s.key);
        setExpandedPath([s]);
      }, 0);
      return () => clearTimeout(timer);
    } else {
      setExpandedParentKey(null);
      setExpandedPath([]);
      return;
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
          const newIndex = Math.min(childActiveIndex + 1, children.length - 1);
          setChildActiveIndex(newIndex);
          const child = children[newIndex];
          if (child?.children?.length) {
            // 展开下一级子面板
            const timer = setTimeout(() => {
              calcChildPos(child.key);
              setExpandedPath(prev => [...prev, child]);
            }, 0);
          } else if (expandedPath.length > 1) {
            // 无子项时收起当前面板
            setExpandedPath(prev => prev.slice(0, -1));
          }
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          const newIndex = Math.max(childActiveIndex - 1, 0);
          setChildActiveIndex(newIndex);
          const child = children[newIndex];
          if (child?.children?.length) {
            const timer = setTimeout(() => {
              calcChildPos(child.key);
              setExpandedPath(prev => [...prev, child]);
            }, 0);
          } else if (expandedPath.length > 1) {
            setExpandedPath(prev => prev.slice(0, -1));
          }
        } else if (e.key === 'ArrowLeft') {
          e.preventDefault();
          // 进入下一级子面板
          const currentChild = children[childActiveIndex];
          if (currentChild?.children?.length) {
            const timer = setTimeout(() => {
              calcChildPos(currentChild.key);
              setExpandedPath(prev => [...prev, currentChild]);
              setChildActiveIndex(0);
            }, 0);
          }
        } else if (e.key === 'ArrowRight') {
          e.preventDefault();
          // 返回上一级（收起当前面板）
          if (expandedPath.length > 1) {
            setExpandedPath(prev => prev.slice(0, -1));
            setChildActiveIndex(0);
          } else {
            setActivePanel('main');
          }
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
        } else if (e.key === 'ArrowRight') {
          e.preventDefault();
          const currentChild = children[childActiveIndex];
          if (currentChild?.children?.length) {
            const timer = setTimeout(() => {
              calcChildPos(currentChild.key);
              setExpandedPath(prev => [...prev, currentChild]);
              setActivePanel('child');
              setChildActiveIndex(0);
            }, 0);
          } else {
            if (expandedPath.length > 1) {
              setExpandedPath(prev => prev.slice(0, -1));
              setChildActiveIndex(0);
            } else {
              setActivePanel('main');
              setChildActiveIndex(-1);
            }
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
      setExpandedPath([]);
      setPanelPositions(new Map());
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

  console.log('selectedValues', selectedValues)
  return (
    <div ref={containerRef} className={`rb:relative rb:w-full rb:min-w-0 rb:max-w-full ${className}`}>
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
                const isConv = root.group === 'CONVERSATION' || root.group === 'SYSTEM' || root.group === 'ENV';
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
          <div className="rb:flex rb:flex-1 rb:min-w-0 rb:max-w-full">
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
          <div className="rb:w-70 rb:h-57.5 rb:overflow-y-auto">
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

      {/* Child panels via portal — supports infinite nesting with separate overlay panels */}
      {open && expandedPath.length > 0 && expandedPath.map((parent, index) => {
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
              [position.useRight ? 'right' : 'left']: position.useRight ? position.horizontal : position.horizontal 
            }}
            onMouseEnter={() => setExpandedParentKey(panelKey)}
          >
            {/* Breadcrumb header */}
            <div className="rb:pb-2 rb:mb-1 rb:font-medium rb:text-[#5B6167] rb-border-b">
              <Flex justify="space-between" align="center" gap={8}>
                <Flex align="center" gap={2}>
                  <span>
                    {expandedPath.slice(0, index + 1).map((item, idx) => (
                      <span key={item.key}>
                        {idx > 0 && '.'}
                        {item.label}
                      </span>
                    ))}
                  </span>
                </Flex>
                <span>{parent.dataType}</span>
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
                    if (child.disabled) return;
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
                  <Flex align="center" gap={8}>
                    {multiple && (
                      <Checkbox checked={isSelected} />
                    )}
                    <span className="rb:font-medium">{child.label}</span>
                  </Flex>
                  <Space size={2}>
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
    </div>
  );
};

export default VariableSelect
