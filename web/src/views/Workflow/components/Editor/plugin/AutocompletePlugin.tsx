/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-23 16:22:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-02 17:12:41
 */
import { useEffect, useState, useRef, type FC } from 'react';
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
}

// Autocomplete plugin for variable suggestions triggered by '/' character
const AutocompletePlugin: FC<{ options: Suggestion[] }> = ({ options }) => {
  const [editor] = useLexicalComposerContext();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [popupPosition, setPopupPosition] = useState({ top: 0, left: 0 });
  const popupRef = useRef<HTMLDivElement>(null);

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
        }

        // Calculate popup position to keep it within viewport bounds
        if (shouldShow) {
          const domSelection = window.getSelection();
          if (domSelection && domSelection.rangeCount > 0) {
            const range = domSelection.getRangeAt(0);
            const rect = range.getBoundingClientRect();

            const popupWidth = 280;
            const popupHeight = 200;
            const viewportWidth = window.innerWidth;
            const viewportHeight = window.innerHeight;

            let left = rect.left;
            let top = rect.top - 10;

            if (left + popupWidth > viewportWidth) {
              left = viewportWidth - popupWidth - 10;
            }
            if (left < 10) {
              left = 10;
            }

            if (top - popupHeight < 10) {
              top = rect.bottom + 10;
              if (top + popupHeight > viewportHeight) {
                top = viewportHeight - popupHeight - 10;
              }
            }

            setPopupPosition({ top, left });
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
        return true;
      },
      COMMAND_PRIORITY_HIGH
    );
  }, [editor]);

  // Insert selected suggestion into editor
  const insertMention = (suggestion: Suggestion) => {
    editor.dispatchCommand(INSERT_VARIABLE_COMMAND, { data: suggestion });
    setShowSuggestions(false);
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

  // Handle Enter key to select suggestion
  useEffect(() => {
    if (!showSuggestions) return;

    const allOptions = Object.values(groupedSuggestions).flat();

    return editor.registerCommand(
      KEY_ENTER_COMMAND,
      (event) => {
        if (showSuggestions && allOptions.length > 0) {
          const selectedOption = allOptions[selectedIndex];
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
  }, [showSuggestions, selectedIndex, groupedSuggestions, insertMention, editor]);

  // Handle keyboard navigation (Arrow Up/Down, Escape)
  useEffect(() => {
    if (!showSuggestions) return;

    const allOptions = Object.values(groupedSuggestions).flat();

    // Navigate down through suggestions, skip disabled items
    const unregisterArrowDown = editor.registerCommand(
      KEY_ARROW_DOWN_COMMAND,
      (event) => {
        if (showSuggestions && allOptions.length > 0) {
          event?.preventDefault();
          setSelectedIndex(prev => {
            let nextIndex = prev + 1;
            while (nextIndex < allOptions.length && allOptions[nextIndex].disabled) {
              nextIndex++;
            }
            const newIndex = nextIndex >= allOptions.length ? prev : nextIndex;
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
        if (showSuggestions && allOptions.length > 0) {
          event?.preventDefault();
          setSelectedIndex(prev => {
            let prevIndex = prev - 1;
            while (prevIndex >= 0 && allOptions[prevIndex].disabled) {
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
  }, [showSuggestions, selectedIndex, groupedSuggestions, editor]);

  if (!showSuggestions) return null;

  if (Object.entries(groupedSuggestions).length === 0) {
    return null
  }
  return (
    <div
      ref={popupRef}
      data-autocomplete-popup="true"
      onMouseDown={(e) => e.preventDefault()}
      className="rb:fixed rb:z-1000 rb:py-1 rb:bg-white rb:rounded-xl rb:min-w-70 rb:max-h-50 rb:overflow-y-auto rb:transform-[translateY(-100%)] rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]"
      style={{
        top: popupPosition.top,
        left: popupPosition.left,
      }}
    >
      <Flex vertical gap={12}>
        {Object.entries(groupedSuggestions).map(([nodeId, nodeOptions]) => {
          const nodeName = nodeOptions[0]?.nodeData?.name || nodeId;
          const nodeIcon = nodeOptions[0]?.nodeData?.icon;
          return (
            <div key={nodeId}>
              <Flex align="center" gap={4} className="rb:px-3! rb:text-[12px] rb:py-1.25! rb:font-medium rb:text-[#5B6167]">
                {nodeIcon && <img
                  src={nodeIcon}
                  className="rb:size-3"
                  alt=""
                />}
                {nodeName}
              </Flex>
              {nodeOptions.map((option) => {
                const globalIndex = Object.values(groupedSuggestions).flat().indexOf(option);
                return (
                  <Flex
                    key={option.key}
                    data-selected={selectedIndex === globalIndex}
                    className="rb:pl-6! rb:pr-3! rb:py-2! "
                    align="center"
                    justify="space-between"
                    style={{
                      cursor: option.disabled ? 'not-allowed' : 'pointer',
                      background: selectedIndex === globalIndex ? '#f0f8ff' : 'white',
                      opacity: option.disabled ? 0.5 : 1,
                    }}
                    onClick={() => !option.disabled && insertMention(option)}
                    onMouseEnter={() => setSelectedIndex(globalIndex)}
                  >
                    <Space size={4}>
                      <span className="rb:text-[#155EEF]">{option.isContext ? '📄' : `{x}`}</span>
                      <span>{option.label}</span>
                    </Space>
                    {option.dataType && (
                      <span className="rb:text-[#5B6167]">
                        {option.dataType}
                      </span>
                    )}
                  </Flex>
                );
              })}
            </div>
          );
        })}
      </Flex>
    </div>
  );
}
export default AutocompletePlugin