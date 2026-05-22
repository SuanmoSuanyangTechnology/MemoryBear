/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-23 16:22:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-19 14:31:07
 */
import { useEffect, useLayoutEffect, useState, useRef, type FC } from 'react';
import { createPortal } from 'react-dom';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getSelection, $isRangeSelection, COMMAND_PRIORITY_HIGH, KEY_ENTER_COMMAND, KEY_ARROW_DOWN_COMMAND, KEY_ARROW_UP_COMMAND, KEY_ESCAPE_COMMAND } from 'lexical';
import { Space, Flex } from 'antd';
import clsx from 'clsx';

import { INSERT_VARIABLE_COMMAND, CLOSE_AUTOCOMPLETE_COMMAND } from '../commands';
import type { NodeProperties } from '../../../types'

// Suggestion item interface for autocomplete dropdown
export interface Suggestion {
  key: string;
  label: string;
  type: string;
  dataType: string;
  value: string;
  group?: string
  nodeData: NodeProperties;
  isContext?: boolean; // Flag for context variable
  disabled?: boolean; // Flag for disabled state
  children?: Suggestion[]; // Sub-variables (e.g. file fields)
  parentLabel?: string; // Parent variable label (for child display)
  default?: any;
  ui_type?: string;
  options?: string[];
  required?: boolean;
}

// Autocomplete plugin for variable suggestions triggered by '/' character
const AutocompletePlugin: FC<{ options: Suggestion[] }> = ({ options }) => {
  const [editor] = useLexicalComposerContext();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [popupPosition, setPopupPosition] = useState({ top: 0, left: 0, anchorBottom: 0 });
  const [expandedParent, setExpandedParent] = useState<Suggestion | null>(null);
  const [childPanelPos, setChildPanelPos] = useState({ top: 0, right: 0 });
  const [activePanel, setActivePanel] = useState<'main' | 'child'>('main');
  const [childActiveIndex, setChildActiveIndex] = useState(-1);
  const popupRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const childItemRefs = useRef<Map<string, HTMLElement>>(new Map());

  // Adjust popup position after render based on actual size
  useLayoutEffect(() => {
    if (!popupRef.current || !showSuggestions) return;
    const { top, left, anchorBottom } = popupPosition;
    const popupHeight = popupRef.current.offsetHeight;
    const popupWidth = popupRef.current.offsetWidth;
    const viewportHeight = window.innerHeight;
    const viewportWidth = window.innerWidth;
    const MARGIN = 10;

    let finalTop: number;
    if (top - popupHeight - MARGIN >= 0) {
      finalTop = top - popupHeight - MARGIN;
    } else {
      finalTop = anchorBottom + MARGIN;
      if (finalTop + popupHeight > viewportHeight - MARGIN) {
        finalTop = viewportHeight - popupHeight - MARGIN;
      }
    }

    let finalLeft = left;
    if (finalLeft + popupWidth > viewportWidth - MARGIN) {
      finalLeft = viewportWidth - popupWidth - MARGIN;
    }
    if (finalLeft < MARGIN) finalLeft = MARGIN;

    if (finalTop !== top || finalLeft !== left) {
      setPopupPosition(prev => ({ ...prev, top: finalTop, left: finalLeft }));
    }
  }, [showSuggestions, popupPosition.anchorBottom]);

  const CHILD_PANEL_HEIGHT = 280;

  const calcChildPanelPos = (key: string) => {
    const el = itemRefs.current.get(key);
    if (!el || !popupRef.current) return;
    const elRect = el.getBoundingClientRect();
    const popupRect = popupRef.current.getBoundingClientRect();
    const actualChildHeight = Math.min(CHILD_PANEL_HEIGHT, popupRect.height);
    const top = Math.max(10, popupRect.bottom - actualChildHeight);
    setChildPanelPos({ top, right: window.innerWidth - elRect.left + 8 });
  };

  const resetState = () => {
    setShowSuggestions(false);
    setExpandedParent(null);
    setChildPanelPos({ top: 0, right: 0 });
    setActivePanel('main');
    setChildActiveIndex(-1);
  };

  // Listen to editor updates and show suggestions when '/' is typed
  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const selection = $getSelection();
        
        if (!selection || !$isRangeSelection(selection)) {
          setShowSuggestions(false);
          return;
        }

        const anchorNode = selection.anchor.getNode();
        const anchorOffset = selection.anchor.offset;
        const nodeText = anchorNode.getTextContent();
        const textBeforeCursor = nodeText.substring(0, anchorOffset);
        const shouldShow = textBeforeCursor.endsWith('/') || 
                          (textBeforeCursor === '/' && anchorOffset === 1);
        
        setShowSuggestions(shouldShow);
        if (!shouldShow) {
          setSelectedIndex(0);
          setExpandedParent(null);
          setChildPanelPos({ top: 0, right: 0 });
          setActivePanel('main');
          setChildActiveIndex(-1);
        }

        if (shouldShow) {
          const domSelection = window.getSelection();
          if (domSelection && domSelection.rangeCount > 0) {
            const range = domSelection.getRangeAt(0);
            const rect = range.getBoundingClientRect();

            const popupWidth = 280;
            const viewportWidth = window.innerWidth;

            let left = rect.left;
            if (left + popupWidth > viewportWidth) {
              left = viewportWidth - popupWidth - 10;
            }
            if (left < 10) left = 10;

            setPopupPosition({ top: rect.top, left, anchorBottom: rect.bottom });
          }
        }
      });
    });
  }, [editor]);

  // Register command to close autocomplete popup
  useEffect(() => {
    return editor.registerCommand(
      CLOSE_AUTOCOMPLETE_COMMAND,
      () => {
        resetState();
        return true;
      },
      COMMAND_PRIORITY_HIGH
    );
  }, [editor]);

  // Insert selected suggestion into editor
  const insertMention = (suggestion: Suggestion) => {
    editor.dispatchCommand(INSERT_VARIABLE_COMMAND, { data: suggestion });
    resetState();
  };

  // Group suggestions by node ID
  const groupedSuggestions = options.reduce((groups: Record<string, Suggestion[]>, suggestion) => {
    const { nodeData } = suggestion
    const nodeId = nodeData?.id as string;
    if (!groups[nodeId]) {
      groups[nodeId] = [];
    }
    groups[nodeId].push(suggestion);
    return groups;
  }, {});

  // Flat list of main-panel items for keyboard navigation
  const flatOptions = Object.values(groupedSuggestions).flat();

  // Sync child panel position when keyboard navigates to a parent with children
  useEffect(() => {
    if (selectedIndex < 0 || selectedIndex >= flatOptions.length) return;
    const s = flatOptions[selectedIndex];
    if (s.children?.length) {
      calcChildPanelPos(s.key);
      setExpandedParent(s);
    } else {
      setExpandedParent(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndex]);

  // Scroll child active item into view
  useEffect(() => {
    if (!expandedParent?.children?.length || childActiveIndex < 0) return;
    const child = expandedParent.children[childActiveIndex];
    if (child) childItemRefs.current.get(child.key)?.scrollIntoView({ block: 'nearest' });
  }, [childActiveIndex, expandedParent]);

  // Handle Enter key to select suggestion
  useEffect(() => {
    if (!showSuggestions) return;

    return editor.registerCommand(
      KEY_ENTER_COMMAND,
      (event) => {
        if (!showSuggestions) return false;
        if (activePanel === 'child' && expandedParent?.children?.length) {
          const child = expandedParent.children[childActiveIndex];
          if (child && !child.disabled) {
            event?.preventDefault();
            insertMention(child);
            return true;
          }
        } else if (flatOptions.length > 0) {
          const selectedOption = flatOptions[selectedIndex];
          if (selectedOption && !selectedOption.disabled) {
            event?.preventDefault();
            insertMention(selectedOption);
            return true;
          }
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH
    );
  }, [showSuggestions, selectedIndex, flatOptions, insertMention, editor, activePanel, childActiveIndex, expandedParent]);

  // Handle keyboard navigation (Arrow Up/Down/Left/Right, Escape)
  useEffect(() => {
    if (!showSuggestions) return;

    const unregisterArrowDown = editor.registerCommand(
      KEY_ARROW_DOWN_COMMAND,
      (event) => {
        if (!showSuggestions) return false;
        event?.preventDefault();
        if (activePanel === 'child' && expandedParent?.children) {
          setChildActiveIndex(i => Math.min(i + 1, expandedParent.children!.length - 1));
        } else {
          setSelectedIndex(prev => {
            let next = prev + 1;
            // skip items that are disabled AND have no children
            while (next < flatOptions.length && flatOptions[next].disabled && !flatOptions[next].children?.length) next++;
            const newIndex = next >= flatOptions.length ? prev : next;
            setTimeout(() => itemRefs.current.get(flatOptions[newIndex]?.key)?.scrollIntoView({ block: 'nearest' }), 0);
            return newIndex;
          });
        }
        return true;
      },
      COMMAND_PRIORITY_HIGH
    );

    const unregisterArrowUp = editor.registerCommand(
      KEY_ARROW_UP_COMMAND,
      (event) => {
        if (!showSuggestions) return false;
        event?.preventDefault();
        if (activePanel === 'child' && expandedParent?.children) {
          setChildActiveIndex(i => Math.max(i - 1, 0));
        } else {
          setSelectedIndex(prev => {
            let prevIdx = prev - 1;
            // skip items that are disabled AND have no children
            while (prevIdx >= 0 && flatOptions[prevIdx].disabled && !flatOptions[prevIdx].children?.length) prevIdx--;
            const newIndex = prevIdx < 0 ? prev : prevIdx;
            setTimeout(() => itemRefs.current.get(flatOptions[newIndex]?.key)?.scrollIntoView({ block: 'nearest' }), 0);
            return newIndex;
          });
        }
        return true;
      },
      COMMAND_PRIORITY_HIGH
    );

    const unregisterEscape = editor.registerCommand(
      KEY_ESCAPE_COMMAND,
      (event) => {
        if (showSuggestions) {
          event?.preventDefault();
          setShowSuggestions(false);
          return true;
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH
    );

    return () => {
      unregisterArrowDown();
      unregisterArrowUp();
      unregisterEscape();
    };
  }, [showSuggestions, selectedIndex, flatOptions, editor, activePanel, childActiveIndex, expandedParent]);

  // Handle ArrowLeft/Right for panel switching via native keydown (lexical doesn't expose these commands)
  useEffect(() => {
    if (!showSuggestions) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        const current = flatOptions[selectedIndex];
        if (activePanel === 'main' && current?.children?.length) {
          e.preventDefault();
          setActivePanel('child');
          setChildActiveIndex(0);
        }
      } else if (e.key === 'ArrowRight') {
        if (activePanel === 'child') {
          e.preventDefault();
          setActivePanel('main');
          setChildActiveIndex(-1);
        }
      }
    };
    document.addEventListener('keydown', handler, true);
    return () => document.removeEventListener('keydown', handler, true);
  }, [showSuggestions, activePanel, selectedIndex, flatOptions]);

  if (!showSuggestions) return null;
  if (Object.entries(groupedSuggestions).length === 0) return null;

  return (
    <>
      <div
        ref={popupRef}
        data-autocomplete-popup="true"
        onMouseDown={(e) => e.preventDefault()}
        className="rb:fixed rb:z-1000 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2"
        style={{
          top: popupPosition.top,
          left: popupPosition.left,
        }}
      >
        <div className="rb:min-w-70 rb:max-h-57.5 rb:overflow-y-auto">
          <Flex vertical gap={12}>
            {Object.entries(groupedSuggestions).map(([nodeId, nodeOptions]) => {
              const nodeName = nodeOptions[0]?.nodeData?.name || nodeId;
              return (
                <div key={nodeId} className="rb:text-[12px]">
                  {nodeName !== 'undefined' &&
                    <div className="rb:px-2 rb:leading-4.25 rb:mb-1.25 rb:font-medium rb:text-[#5B6167]">
                      {nodeName}
                    </div>
                  }
                  <Flex vertical gap={2}>
                  {nodeOptions.map((option) => {
                    const globalIndex = flatOptions.indexOf(option);
                    const isExpanded = expandedParent?.key === option.key;
                    const hasChildren = !!option.children?.length;
                    const isActive = activePanel === 'main' && selectedIndex === globalIndex;
                    return (
                      <Flex
                        key={option.key}
                        ref={(el) => { if (el) itemRefs.current.set(option.key, el); }}
                        className={clsx("rb:px-2! rb:py-0.75! rb:rounded-sm rb:leading-4.5 rb:text-[#5B6167] rb:hover:bg-[#F6F6F6]", {
                          'rb:bg-[#F6F6F6]': isActive || isExpanded,
                          'rb:cursor-not-allowed rb:opacity-65': option.disabled,
                          'rb:cursor-pointer': !option.disabled,
                        })}
                        align="center"
                        justify="space-between"
                        onClick={() => {
                          if (option.disabled && !hasChildren) return;
                          if (!option.disabled) insertMention(option);
                          if (hasChildren) {
                            calcChildPanelPos(option.key);
                            setExpandedParent(option);
                          }
                        }}
                        onMouseEnter={() => {
                          setSelectedIndex(globalIndex);
                          setActivePanel('main');
                          setChildActiveIndex(-1);
                          if (hasChildren) {
                            calcChildPanelPos(option.key);
                            setExpandedParent(option);
                          } else {
                            setExpandedParent(null);
                          }
                        }}
                      >
                        {option.label &&
                          <div className="rb:font-medium">
                            <span className="rb:text-[#155EEF]">{`{x}`}</span> {option.label}
                          </div>
                        }
                        <Space size={2}>
                          {option.dataType && <span>{option.dataType}</span>}
                          {hasChildren && <div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:rotate-90"></div>}
                        </Space>
                      </Flex>
                    );
                  })}
                  </Flex>
                </div>
              );
            })}
          </Flex>
        </div>
      </div>

      {/* Child variables panel - fixed positioned via portal to avoid clipping */}
      {expandedParent?.children?.length && createPortal(
        <div
          onMouseDown={(e) => e.preventDefault()}
          className="rb:min-w-70 rb:max-h-57.5 rb:overflow-y-auto rb:text-[12px] rb:fixed rb:z-1000 rb:bg-white rb:rounded-lg rb:border-[0.5px] rb:border-[#EBEBEB] rb:shadow-[0px_2px_6px_0px_rgba(0,0,0,0.1)] rb:py-3 rb:px-2"
          style={{ top: childPanelPos.top, right: childPanelPos.right }}
          onMouseEnter={() => setActivePanel('child')}
          onMouseLeave={() => { setActivePanel('main'); setChildActiveIndex(-1); }}
        >
          <div className="rb:pb-2 rb:mb-1 rb:font-medium rb:text-[#5B6167] rb-border-b">
            <Flex justify="space-between" align="center" gap={8}>
              <span>{expandedParent.nodeData.name}.{expandedParent.label}</span>
              <span>{expandedParent.dataType}</span>
            </Flex>
          </div>
          {expandedParent.children.map((child, ci) => {
            const isChildActive = activePanel === 'child' && ci === childActiveIndex;
            return (
              <Flex
                key={child.key}
                ref={(el) => { if (el) childItemRefs.current.set(child.key, el); }}
                className={clsx("rb:px-2! rb:py-0.75! rb:rounded-sm rb:leading-4.5 rb:text-[#5B6167] rb:hover:bg-[#F6F6F6]", {
                  'rb:bg-[#F6F6F6]': isChildActive,
                  'rb:cursor-not-allowed rb:opacity-65': child.disabled,
                  'rb:cursor-pointer': !child.disabled,
                })}
                align="center"
                justify="space-between"
                onClick={() => !child.disabled && insertMention(child)}
                onMouseEnter={() => { setActivePanel('child'); setChildActiveIndex(ci); }}
              >
                <span className="rb:font-medium">
                  <span className="rb:text-[#155EEF]">{`{x}`}</span> {child.label}
                </span>
                {child.dataType && <span>{child.dataType}</span>}
              </Flex>
            );
          })}
        </div>,
        document.body
      )}
    </>
  );
}
export default AutocompletePlugin
