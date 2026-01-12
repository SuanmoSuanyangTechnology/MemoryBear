import { useEffect, useRef } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $createParagraphNode, $createTextNode } from 'lexical';

import { $createVariableNode } from '../nodes/VariableNode';
import { type Suggestion } from '../plugin/AutocompletePlugin'

interface InitialValuePluginProps {
  value: string;
  options?: Suggestion[];
  enableJinja2?: boolean;
}

const InitialValuePlugin: React.FC<InitialValuePluginProps> = ({ value, options = [], enableJinja2 = false }) => {
  const [editor] = useLexicalComposerContext();
  const prevValueRef = useRef<string>('');
  const isUserInputRef = useRef(false);

  useEffect(() => {
    // 监听编辑器变化，标记是否为用户输入
    const removeListener = editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const root = $getRoot();
        const textContent = root.getTextContent();
        if (textContent !== prevValueRef.current) {
          isUserInputRef.current = true;
        }
      });
    });

    return removeListener;
  }, [editor]);

  useEffect(() => {
    if (value !== prevValueRef.current && !isUserInputRef.current) {
      editor.update(() => {
        const root = $getRoot();
        root.clear();

        const parts = value.split(/(\{\{[^}]+\}\})/);

        if (enableJinja2) {
          // Handle newlines properly in Jinja2 mode
          const lines = value.split('\n');
          lines.forEach((line) => {
            const paragraph = $createParagraphNode();
            paragraph.append($createTextNode(line));
            root.append(paragraph);
          });
        } else {
          const paragraph = $createParagraphNode();
          parts.forEach(part => {
            const match = part.match(/^\{\{([^.]+)\.([^}]+)\}\}$/);
            const contextMatch = part.match(/^\{\{context\}\}$/);
            const conversationMatch = part.match(/^\{\{conv\.([^}]+)\}\}$/);

            if (contextMatch) {
              const contextSuggestion = options.find(s => s.isContext && s.label === 'context');
              if (contextSuggestion) {
                paragraph.append($createVariableNode(contextSuggestion));
              } else {
                paragraph.append($createTextNode(part));
              }
              return
            }
            
            if (conversationMatch) {
              const [_, variableName] = conversationMatch;
              const conversationSuggestion = options.find(s => 
                s.group === 'CONVERSATION' && s.label === variableName
              );
              if (conversationSuggestion) {
                paragraph.append($createVariableNode(conversationSuggestion));
              } else {
                paragraph.append($createTextNode(part));
              }
              return
            }
            
            if (match) {
              const [_, nodeId, label] = match;

              const suggestion = options.find(s => {
                if (nodeId === 'sys') {
                  return s.nodeData.type === 'start' && s.label === `sys.${label}`
                }
                return s.nodeData.id === nodeId && s.label === label
              });

              if (suggestion) {
                paragraph.append($createVariableNode(suggestion));
              } else {
                paragraph.append($createTextNode(part));
              }
            } else if (part) {
              paragraph.append($createTextNode(part));
            }
          });
          root.append(paragraph);
        }
      }, { discrete: true });
    }
    
    prevValueRef.current = value;
    isUserInputRef.current = false;
  }, [value, options, editor, enableJinja2]);

  return null;
};

export default InitialValuePlugin;