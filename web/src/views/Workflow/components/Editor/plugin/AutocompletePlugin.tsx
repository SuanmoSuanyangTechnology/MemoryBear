/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-23 16:22:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 15:53:57
 */
import { useEffect, useState, useRef, type FC } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getSelection, $isRangeSelection, $isTextNode, COMMAND_PRIORITY_HIGH, KEY_ENTER_COMMAND, KEY_ARROW_DOWN_COMMAND, KEY_ARROW_UP_COMMAND, KEY_ESCAPE_COMMAND } from 'lexical';
import { Space } from 'antd';

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
const AutocompletePlugin: FC<{ options: Suggestion[], enableJinja2?: boolean }> = ({ options, enableJinja2 = false }) => {
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
    if (enableJinja2) {
      // In Jinja2 mode, insert {{variable}} format text
      editor.update(() => {
        const selection = $getSelection();
        if ($isRangeSelection(selection)) {
          const anchorNode = selection.anchor.getNode();
          const anchorOffset = selection.anchor.offset;
          const nodeText = anchorNode.getTextContent();
          
          // Remove trigger character '/'
          const textBefore = nodeText.substring(0, anchorOffset - 1);
          const textAfter = nodeText.substring(anchorOffset);
          const newText = textBefore + `{{${suggestion.value}}}` + textAfter;
          
          if ($isTextNode(anchorNode)) {
            anchorNode.setTextContent(newText);
          }
          
          // Set cursor position after inserted text
          const newOffset = textBefore.length + `{{${suggestion.value}}}`.length;
          selection.anchor.offset = newOffset;
          selection.focus.offset = newOffset;
        }
      });
    } else {
      // In normal mode, use VariableNode
      editor.dispatchCommand(INSERT_VARIABLE_COMMAND, { data: suggestion });
    }
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
      style={{
        position: 'fixed',
        top: popupPosition.top,
        left: popupPosition.left,
        zIndex: 1000,
        background: 'white',
        border: '1px solid #d9d9d9',
        borderRadius: '6px',
        boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
        minWidth: '280px',
        maxHeight: '200px',
        overflowY: 'auto',
        transform: 'translateY(-100%)',
      }}
    >
      {Object.entries(groupedSuggestions).map(([nodeId, nodeOptions], groupIndex) => {
        const nodeName = nodeOptions[0]?.nodeData?.name || nodeId;
        return (
          <div key={nodeId}>
            {/* Divider between groups */}
            {groupIndex > 0 && <div style={{ height: '1px', background: '#f0f0f0', margin: '4px 0' }} />}
            {/* Group header with node name */}
            <div style={{ padding: '4px 12px', fontSize: '12px', color: '#999', fontWeight: 'bold' }}>
              {nodeName}
            </div>
            {nodeOptions.map((option) => {
              const globalIndex = Object.values(groupedSuggestions).flat().indexOf(option);
              return (
                <div
                  key={option.key}
                  data-selected={selectedIndex === globalIndex}
                  style={{
                    padding: '8px 12px',
                    cursor: option.disabled ? 'not-allowed' : 'pointer',
                    background: selectedIndex === globalIndex ? '#f0f8ff' : 'white',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
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
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
export default AutocompletePlugin