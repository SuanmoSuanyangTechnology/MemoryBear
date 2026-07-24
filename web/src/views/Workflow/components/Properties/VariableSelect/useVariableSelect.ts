import { useState, useRef, useEffect, useLayoutEffect } from 'react';

import type { Suggestion, PanelPos } from '../../Editor/plugin/autocomplete/types';
import { calcSmartPanelPos } from '../../Editor/plugin/autocomplete/smartPos';

interface UseVariableSelectParams {
  options: Suggestion[];
  value?: string | string[];
  multiple: boolean;
  filterBooleanType: boolean;
  onChange?: (value?: string | string[], option?: Suggestion | Suggestion[] | undefined) => void;
}

/**
 * All state, positioning, keyboard navigation and derived data for VariableSelect.
 * Extracted so the presentational trigger/dropdown/child-panel pieces stay small.
 */
export function useVariableSelect({
  options,
  value,
  multiple,
  filterBooleanType,
  onChange,
}: UseVariableSelectParams) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [expandedParentKey, setExpandedParentKey] = useState<string | null>(null);
  const [expandedPath, setExpandedPath] = useState<Suggestion[]>([]);
  const [panelPositions, setPanelPositions] = useState<Map<string, PanelPos>>(new Map());
  const [activeIndex, setActiveIndex] = useState<number>(-1);
  const [activePanel, setActivePanel] = useState<'main' | 'child'>('main');
  const [childActiveIndex, setChildActiveIndex] = useState<number>(-1);
  const [dropdownPos, setDropdownPos] = useState({ top: 0, left: 0, width: 0 });
  const [childPanelPos, setChildPanelPos] = useState<PanelPos>({ top: 0, horizontal: 0, useRight: true });
  const containerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const childItemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const activeKeyRef = useRef<string | null>(null);

  const calcChildPos = (key: string, fromMainPanel: boolean = false) => {
    const anchorTop = dropdownRef.current ? dropdownRef.current.getBoundingClientRect().top : null;
    if (fromMainPanel) {
      const el = itemRefs.current.get(key);
      if (!el) return;
      setChildPanelPos(calcSmartPanelPos(el.getBoundingClientRect(), anchorTop));
    } else {
      const el = childItemRefs.current.get(key);
      if (!el) return;
      setPanelPositions(prev => new Map(prev).set(key, calcSmartPanelPos(el.getBoundingClientRect(), anchorTop)));
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

  // Flat list of all visible selectable items (main panel only, no children expanded inline)
  const flatItems = Object.values(filteredGroups).flat();

  const closeAndReset = () => {
    setOpen(false);
    setSearch('');
    setExpandedParentKey(null);
    setExpandedPath([]);
    setPanelPositions(new Map());
    setChildPanelPos({ top: 0, horizontal: 0, useRight: true });
  };

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

  useEffect(() => {
    if (!expandedParentKey) return;
    calcChildPos(expandedParentKey);
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
        closeAndReset();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

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
            // Expand the next-level child panel
            setTimeout(() => {
              calcChildPos(child.key);
              setExpandedPath(prev => [...prev, child]);
            }, 0);
          } else if (expandedPath.length > 1) {
            // Collapse the current panel when there are no children
            setExpandedPath(prev => prev.slice(0, -1));
          }
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          const newIndex = Math.max(childActiveIndex - 1, 0);
          setChildActiveIndex(newIndex);
          const child = children[newIndex];
          if (child?.children?.length) {
            setTimeout(() => {
              calcChildPos(child.key);
              setExpandedPath(prev => [...prev, child]);
            }, 0);
          } else if (expandedPath.length > 1) {
            setExpandedPath(prev => prev.slice(0, -1));
          }
        } else if (e.key === 'ArrowLeft') {
          e.preventDefault();
          // Enter the next-level child panel
          const currentChild = children[childActiveIndex];
          if (currentChild?.children?.length) {
            setTimeout(() => {
              calcChildPos(currentChild.key);
              setExpandedPath(prev => [...prev, currentChild]);
              setChildActiveIndex(0);
            }, 0);
          }
        } else if (e.key === 'ArrowRight') {
          e.preventDefault();
          // Go back one level (collapse the current panel)
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
            setTimeout(() => {
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
  }, [open, activeIndex, activePanel, childActiveIndex, flatItems, expandedParent]);

  return {
    // state + setters
    open, setOpen,
    search, setSearch,
    expandedPath, setExpandedPath,
    setExpandedParentKey,
    expandedParent,
    panelPositions,
    childPanelPos,
    dropdownPos,
    activeIndex,
    activePanel,
    childActiveIndex,
    // refs
    containerRef,
    dropdownRef,
    itemRefs,
    childItemRefs,
    // derived
    filteredOptions,
    suggestionMap,
    suggestionParentMap,
    selectedValues,
    selectedSuggestion,
    parentOfSelected,
    filteredGroups,
    flatItems,
    // handlers
    calcChildPos,
    handleSelect,
    handleClear,
  };
}

export type VariableSelectState = ReturnType<typeof useVariableSelect>;
