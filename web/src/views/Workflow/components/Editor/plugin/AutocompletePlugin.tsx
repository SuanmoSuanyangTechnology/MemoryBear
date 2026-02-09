import { useEffect, useState, type FC } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getSelection, $isRangeSelection, $isTextNode, COMMAND_PRIORITY_HIGH, KEY_ENTER_COMMAND, KEY_ARROW_DOWN_COMMAND, KEY_ARROW_UP_COMMAND, KEY_ESCAPE_COMMAND } from 'lexical';

import { INSERT_VARIABLE_COMMAND } from '../commands';
import type { NodeProperties } from '../../../types'

export interface Suggestion {
  key: string;
  label: string;
  type: string;
  dataType: string;
  value: string;
  group?: string
  nodeData: NodeProperties;
  isContext?: boolean; // 标记是否为context变量
  disabled?: boolean; // 标记是否禁用
}

const AutocompletePlugin: FC<{ options: Suggestion[], enableJinja2?: boolean }> = ({ options, enableJinja2 = false }) => {
  const [editor] = useLexicalComposerContext();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [popupPosition, setPopupPosition] = useState({ top: 0, left: 0 });

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

  const insertMention = (suggestion: Suggestion) => {
    if (enableJinja2) {
      // 在jinja2模式下，插入{{variable}}格式的文本
      editor.update(() => {
        const selection = $getSelection();
        if ($isRangeSelection(selection)) {
          const anchorNode = selection.anchor.getNode();
          const anchorOffset = selection.anchor.offset;
          const nodeText = anchorNode.getTextContent();
          
          // 移除触发字符'/'
          const textBefore = nodeText.substring(0, anchorOffset - 1);
          const textAfter = nodeText.substring(anchorOffset);
          const newText = textBefore + `{{${suggestion.value}}}` + textAfter;
          
          if ($isTextNode(anchorNode)) {
            anchorNode.setTextContent(newText);
          }
          
          // 设置光标位置到插入文本之后
          const newOffset = textBefore.length + `{{${suggestion.value}}}`.length;
          selection.anchor.offset = newOffset;
          selection.focus.offset = newOffset;
        }
      });
    } else {
      // 普通模式下使用VariableNode
      editor.dispatchCommand(INSERT_VARIABLE_COMMAND, { data: suggestion });
    }
    setShowSuggestions(false);
  };

  const groupedSuggestions = options.reduce((groups: Record<string, any[]>, suggestion) => {
    const { nodeData } = suggestion
    const nodeId = nodeData.id as string;
    if (!groups[nodeId]) {
      groups[nodeId] = [];
    }
    groups[nodeId].push(suggestion);
    return groups;
  }, {});

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

  useEffect(() => {
    if (!showSuggestions) return;

    const allOptions = Object.values(groupedSuggestions).flat();

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
            return nextIndex >= allOptions.length ? prev : nextIndex;
          });
          return true;
        }
        return false;
      },
      COMMAND_PRIORITY_HIGH
    );

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
            return prevIndex < 0 ? prev : prevIndex;
          });
          return true;
        }
        return false;
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
  }, [showSuggestions, selectedIndex, groupedSuggestions, editor]);

  if (!showSuggestions) return null;

  if (Object.entries(groupedSuggestions).length === 0) {
    return null
  }
  return (
    <div
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
            {groupIndex > 0 && <div style={{ height: '1px', background: '#f0f0f0', margin: '4px 0' }} />}
            <div style={{ padding: '4px 12px', fontSize: '12px', color: '#999', fontWeight: 'bold' }}>
              {nodeName}
            </div>
            {nodeOptions.map((option) => {
              const globalIndex = Object.values(groupedSuggestions).flat().indexOf(option);
              return (
                <div
                  key={option.key}
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
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span
                      style={{
                        background: option.isContext ? '#722ed1' :
                          option.type === 'system' ? '#1890ff' : '#52c41a',
                        color: 'white',
                        padding: '2px 6px',
                        borderRadius: '4px',
                        fontSize: '12px',
                        minWidth: '16px',
                        textAlign: 'center',
                      }}
                    >
                      {option.isContext ? '📄' :
                        option.type === 'system' ? 'x' : 'x'}
                    </span>
                    <span style={{ fontSize: '14px' }}>{option.label}</span>
                  </div>
                  {option.dataType && (
                    <span style={{ fontSize: '12px', color: '#999' }}>
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