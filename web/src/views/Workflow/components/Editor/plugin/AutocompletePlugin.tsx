/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-23 16:22:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-02 17:12:41
 */
import { useEffect, useLayoutEffect, useState, useRef, type FC } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getSelection, $isRangeSelection, COMMAND_PRIORITY_HIGH, KEY_ENTER_COMMAND, KEY_ARROW_DOWN_COMMAND, KEY_ARROW_UP_COMMAND, KEY_ESCAPE_COMMAND } from 'lexical';
import { Space, Flex } from 'antd';

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
}

// Autocomplete plugin for variable suggestions triggered by '/' character
const AutocompletePlugin: FC<{ options: Suggestion[] }> = ({ options }) => {
  const [editor] = useLexicalComposerContext();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [popupPosition, setPopupPosition] = useState({ top: 0, left: 0, anchorBottom: 0 });
  const [expandedParent, setExpandedParent] = useState<Suggestion | null>(null);
  const [childPanelTop, setChildPanelTop] = useState(0);
  const popupRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Map<string, HTMLElement>>(new Map());

  // Adjust popup position after render based on actual height
  useLayoutEffect(() => {
    if (!popupRef.current || !showSuggestions) return;
    const { top, anchorBottom } = popupPosition;
    const popupHeight = popupRef.current.offsetHeight;
    const viewportHeight = window.innerHeight;
    const MARGIN = 10;

    let finalTop: number;
    if (top - popupHeight - MARGIN >= 0) {
      // Enough space above: show above cursor
      finalTop = top - popupHeight - MARGIN;
    } else {
      // Not enough space above: show below cursor
      finalTop = anchorBottom + MARGIN;
      if (finalTop + popupHeight > viewportHeight - MARGIN) {
        finalTop = viewportHeight - popupHeight - MARGIN;
      }
    }

    if (finalTop !== top) {
      setPopupPosition(prev => ({ ...prev, top: finalTop }));
    }
  }, [showSuggestions, popupPosition.anchorBottom]);

  const CHILD_PANEL_HEIGHT = 280; // max-h-60 (240) + header (~40)

  const calcChildPanelTop = (elRect: DOMRect, popupRect: DOMRect) => {
    const relativeTop = elRect.top - popupRect.top;
    const absoluteBottom = popupRect.top + relativeTop + CHILD_PANEL_HEIGHT;
    const overflow = absoluteBottom - (window.innerHeight - 10);
    return overflow > 0 ? relativeTop - overflow : relativeTop;
  };

  const scrollSelectedIntoView = () => {
    if (!popupRef.current) return;
    
    const selectedElement = popupRef.current.querySelector('[data-selected="true"]');
    if (!selectedElement) return;
    
    const container = popupRef.current;
    const element = selectedElement as HTMLElement;
    
    const containerRect = container.getBoundingClientRect();
    const elementRect = element.getBoundingClientRect();
    
    if (elementRect.bottom > containerRect.bottom) {
      container.scrollTop += elementRect.bottom - containerRect.bottom;
    } else if (elementRect.top < containerRect.top) {
      container.scrollTop -= containerRect.top - elementRect.top;
    }
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
        
        // Get the text content of the current node
        const nodeText = anchorNode.getTextContent();
        
        // Check if we have a '/' at the current position or after line break
        const textBeforeCursor = nodeText.substring(0, anchorOffset);
        const shouldShow = textBeforeCursor.endsWith('/') || 
                          (textBeforeCursor === '/' && anchorOffset === 1);
        
        setShowSuggestions(shouldShow);
        if (!shouldShow) {
          setSelectedIndex(0);
          setExpandedParent(null);
          setChildPanelTop(0);
        }

        // Calculate popup position to keep it within viewport bounds
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
        setShowSuggestions(false);
        setExpandedParent(null);
        setChildPanelTop(0);
        return true;
      },
      COMMAND_PRIORITY_HIGH
    );
  }, [editor]);

  // Insert selected suggestion into editor
  const insertMention = (suggestion: Suggestion) => {
    editor.dispatchCommand(INSERT_VARIABLE_COMMAND, { data: suggestion });
    setShowSuggestions(false);
    setExpandedParent(null);
    setChildPanelTop(0);
  };

  // Group suggestions by node ID
  const groupedSuggestions = options.reduce((groups: Record<string, Suggestion[]>, suggestion) => {
    const { nodeData } = suggestion
    const nodeId = nodeData.id as string;
    if (!groups[nodeId]) {
      groups[nodeId] = [];
    }
    groups[nodeId].push(suggestion);
    return groups;
  }, {});

  // Flat list for keyboard navigation
  const flatOptions = Object.values(groupedSuggestions).flat().flatMap(option => {
    if (option.key === expandedParent?.key && option.children?.length) {
      return [option, ...option.children];
    }
    return [option];
  });

  // Handle Enter key to select suggestion
  useEffect(() => {
    if (!showSuggestions) return;

    return editor.registerCommand(
      KEY_ENTER_COMMAND,
      (event) => {
        if (showSuggestions && flatOptions.length > 0) {
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
  }, [showSuggestions, selectedIndex, flatOptions, insertMention, editor]);

  // Handle keyboard navigation (Arrow Up/Down, Escape)
  useEffect(() => {
    if (!showSuggestions) return;

    // Navigate down through suggestions, skip disabled items
    const unregisterArrowDown = editor.registerCommand(
      KEY_ARROW_DOWN_COMMAND,
      (event) => {
        if (showSuggestions && flatOptions.length > 0) {
          event?.preventDefault();
          setSelectedIndex(prev => {
            let nextIndex = prev + 1;
            while (nextIndex < flatOptions.length && flatOptions[nextIndex].disabled) {
              nextIndex++;
            }
            const newIndex = nextIndex >= flatOptions.length ? prev : nextIndex;
            setTimeout(() => scrollSelectedIntoView(), 0);
            return newIndex;
          });
          return true;
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH
    );

    // Navigate up through suggestions, skip disabled items
    const unregisterArrowUp = editor.registerCommand(
      KEY_ARROW_UP_COMMAND,
      (event) => {
        if (showSuggestions && flatOptions.length > 0) {
          event?.preventDefault();
          setSelectedIndex(prev => {
            let prevIndex = prev - 1;
            while (prevIndex >= 0 && flatOptions[prevIndex].disabled) {
              prevIndex--;
            }
            const newIndex = prevIndex < 0 ? prev : prevIndex;
            setTimeout(() => scrollSelectedIntoView(), 0);
            return newIndex;
          });
          return true;
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH
    );

    // Close suggestions on Escape key
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
  }, [showSuggestions, selectedIndex, flatOptions, editor]);

  if (!showSuggestions) return null;

  if (Object.entries(groupedSuggestions).length === 0) {
    return null
  }
  return (
    <div
      ref={popupRef}
      data-autocomplete-popup="true"
      onMouseDown={(e) => e.preventDefault()}
      className="rb:fixed rb:z-1000 rb:bg-white rb:rounded-xl rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]"
      style={{
        top: popupPosition.top,
        left: popupPosition.left,
      }}
    >
      <div className="rb:py-1 rb:min-w-70 rb:max-h-50 rb:overflow-y-auto">
      <Flex vertical gap={12}>
        {Object.entries(groupedSuggestions).map(([nodeId, nodeOptions]) => {
          const nodeName = nodeOptions[0]?.nodeData?.name || nodeId;
          const nodeIcon = nodeOptions[0]?.nodeData?.icon;
          return (
            <div key={nodeId}>
              <Flex align="center" gap={4} className="rb:px-3! rb:text-[12px] rb:py-1.25! rb:font-medium rb:text-[#5B6167]">
                {nodeIcon && <div className={`rb:size-3 rb:bg-cover ${nodeIcon}`} />}
                {nodeName}
              </Flex>
              {nodeOptions.map((option) => {
                const globalIndex = flatOptions.indexOf(option);
                const isExpanded = expandedParent?.key === option.key;
                const hasChildren = !!option.children?.length;
                return (
                  <Flex
                    key={option.key}
                    ref={(el) => { if (el) itemRefs.current.set(option.key, el); }}
                    data-selected={selectedIndex === globalIndex}
                    className="rb:pl-6! rb:pr-3! rb:py-2!"
                    align="center"
                    justify="space-between"
                    style={{
                      cursor: option.disabled ? 'not-allowed' : 'pointer',
                      background: (selectedIndex === globalIndex || isExpanded) ? '#f0f8ff' : 'white',
                      opacity: option.disabled ? 0.5 : 1,
                    }}
                    onClick={() => {
                      if (option.disabled) return;
                      insertMention(option);
                    }}
                    onMouseEnter={() => {
                      setSelectedIndex(globalIndex);
                      if (hasChildren) {
                        const el = itemRefs.current.get(option.key);
                        if (el && popupRef.current) {
                          const elRect = el.getBoundingClientRect();
                          const popupRect = popupRef.current.getBoundingClientRect();
                          setChildPanelTop(calcChildPanelTop(elRect, popupRect));
                        }
                        setExpandedParent(option);
                      } else {
                        setExpandedParent(null);
                      }
                    }}
                  >
                    <Space size={4}>
                      <span className="rb:text-[#155EEF]">{option.isContext ? '📄' : `{x}`}</span>
                      <span>{option.label}</span>
                    </Space>
                    <Space size={4}>
                      {option.dataType && <span className="rb:text-[#5B6167]">{option.dataType}</span>}
                      {hasChildren && <span className="rb:text-[#5B6167] rb:ml-1">›</span>}
                    </Space>
                  </Flex>
                );
              })}
            </div>
          );
        })}
      </Flex>
      </div>
      {/* Child variables panel - floats to the left */}
      {expandedParent?.children?.length && (
        <div
          className="rb:absolute rb:bg-white rb:rounded-xl rb:py-1 rb:min-w-60 rb:max-h-60 rb:overflow-y-auto rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]"
          style={{
            top: childPanelTop,
            right: 'calc(100% + 8px)',
            transform: 'translateY(-8px)',
          }}
          onMouseEnter={() => setExpandedParent(expandedParent)}
        >
          {/* Header */}
          <div className="rb:px-3 rb:py-2 rb:text-[12px] rb:font-medium rb:text-[#5B6167] rb:border-b rb:border-[#F0F0F0]">
            <Flex justify="space-between" align="center">
              <span>{expandedParent.nodeData.name}.{expandedParent.label}</span>
              <span>{expandedParent.dataType}</span>
            </Flex>
          </div>
          {expandedParent.children.map((child) => {
            const childIndex = flatOptions.indexOf(child);
            return (
              <Flex
                key={child.key}
                data-selected={selectedIndex === childIndex}
                className="rb:px-3! rb:py-2!"
                align="center"
                justify="space-between"
                style={{
                  cursor: child.disabled ? 'not-allowed' : 'pointer',
                  background: selectedIndex === childIndex ? '#f0f8ff' : 'white',
                  opacity: child.disabled ? 0.5 : 1,
                }}
                onClick={() => !child.disabled && insertMention(child)}
                onMouseEnter={() => setSelectedIndex(childIndex)}
              >
                <span>{child.label}</span>
                {child.dataType && <span className="rb:text-[#5B6167]">{child.dataType}</span>}
              </Flex>
            );
          })}
        </div>
      )}
    </div>
  );
}
export default AutocompletePlugin