import { useEffect, useState, type FC } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $getSelection } from 'lexical';

import { INSERT_VARIABLE_COMMAND } from '../commands';
import type { NodeProperties } from '../../../types'

export interface Suggestion {
  key: string;
  label: string;
  type: string;
  dataType: string;
  value: string;
  nodeData: NodeProperties
}

const AutocompletePlugin: FC<{ suggestions: Suggestion[] }> = ({ suggestions }) => {
  const [editor] = useLexicalComposerContext();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [popupPosition, setPopupPosition] = useState({ top: 0, left: 0 });

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const root = $getRoot();
        const text = root.getTextContent();
        const shouldShow = text.includes('/');
        setShowSuggestions(shouldShow);

        if (shouldShow) {
          const selection = $getSelection();
          if (selection) {
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
        }
      });
    });
  }, [editor]);

  const insertMention = (suggestion: Suggestion) => {
    editor.dispatchCommand(INSERT_VARIABLE_COMMAND, { data: suggestion });
    setShowSuggestions(false);
  };

  if (!showSuggestions) return null;

  // Group suggestions by node id
  const groupedSuggestions = suggestions.reduce((groups: Record<string, any[]>, suggestion) => {
    const { nodeData } = suggestion
    const nodeId = nodeData.id as string;
    if (!groups[nodeId]) {
      groups[nodeId] = [];
    }
    groups[nodeId].push(suggestion);
    return groups;
  }, {});

  return (
    <div
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
            {nodeOptions.map((option, index) => {
              const globalIndex = Object.values(groupedSuggestions).flat().indexOf(option);
              return (
                <div
                  key={option.key}
                  style={{
                    padding: '8px 12px',
                    cursor: 'pointer',
                    background: selectedIndex === globalIndex ? '#f0f8ff' : 'white',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                  }}
                  onClick={() => insertMention(option)}
                  onMouseEnter={() => setSelectedIndex(globalIndex)}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span
                      style={{
                        background: option.type === 'context' ? '#722ed1' :
                          option.type === 'system' ? '#1890ff' : '#52c41a',
                        color: 'white',
                        padding: '2px 6px',
                        borderRadius: '4px',
                        fontSize: '12px',
                        minWidth: '16px',
                        textAlign: 'center',
                      }}
                    >
                      {option.type === 'context' ? '📄' :
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