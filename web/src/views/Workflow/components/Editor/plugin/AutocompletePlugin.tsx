import { useEffect, useState, type FC } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $createTextNode, $createParagraphNode, $setSelection, $createRangeSelection, $getSelection } from 'lexical';
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

              // Calculate popup dimensions
              const popupWidth = 280;
              const popupHeight = 200;

              // Get viewport dimensions
              const viewportWidth = window.innerWidth;
              const viewportHeight = window.innerHeight;

              // Calculate position with viewport constraints
              let left = rect.left;
              let top = rect.top - 10;

              // Adjust horizontal position if popup would overflow
              if (left + popupWidth > viewportWidth) {
                left = viewportWidth - popupWidth - 10;
              }
              if (left < 10) {
                left = 10;
              }

              // Adjust vertical position if popup would overflow
              if (top - popupHeight < 10) {
                // Show below cursor if not enough space above
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

  const insertMention = (suggestion: any) => {
    editor.update(() => {
      const root = $getRoot();
      const text = root.getTextContent();
      const lastSlashIndex = text.lastIndexOf('/');
      const beforeSlash = text.slice(0, lastSlashIndex);
      const afterSlash = text.slice(lastSlashIndex + 1);
      const insertedText = `{{${suggestion.value}}} `;
      const newText = beforeSlash + insertedText + afterSlash;
      const cursorPosition = beforeSlash.length + insertedText.length;

      root.clear();
      const paragraph = $createParagraphNode();
      paragraph.append($createTextNode(newText));
      root.append(paragraph);

      // Set cursor after the inserted text
      const textNode = paragraph.getFirstChild();
      if (textNode) {
        const selection = $createRangeSelection();
        selection.anchor.set(textNode.getKey(), cursorPosition, 'text');
        selection.focus.set(textNode.getKey(), cursorPosition, 'text');
        $setSelection(selection);
      }
    });
    setShowSuggestions(false);
  };

  if (!showSuggestions) return null;

  // Group suggestions by node name
  const groupedSuggestions = suggestions.reduce((groups: Record<string, any[]>, suggestion) => {
    const { nodeData } = suggestion
    const nodeName = (nodeData.name || nodeData.id) as string;
    if (!groups[nodeName]) {
      groups[nodeName] = [];
    }
    groups[nodeName].push(suggestion);
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
      {Object.entries(groupedSuggestions).map(([nodeName, nodeOptions], groupIndex) => (
        <div key={nodeName}>
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
      ))}
    </div>
  );
}
export default AutocompletePlugin