import { useEffect, useRef } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $createParagraphNode, $createTextNode } from 'lexical';

import { $createVariableNode } from '../nodes/VariableNode';
import { type Suggestion } from '../plugin/AutocompletePlugin'

interface InitialValuePluginProps {
  value: string;
  options?: Suggestion[];
  enableLineNumbers?: boolean;
}

const InitialValuePlugin: React.FC<InitialValuePluginProps> = ({ value, options = [], enableLineNumbers = false }) => {
  const [editor] = useLexicalComposerContext();
  const prevValueRef = useRef<string>('');
  const prevEnableLineNumbersRef = useRef<boolean>(enableLineNumbers);
  const isUserInputRef = useRef(false);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    const removeListener = editor.registerUpdateListener(({ editorState, tags }) => {
      if (tags.has('programmatic')) return;
      editorState.read(() => {
        const root = $getRoot();
        const textContent = root.getTextContent();
        if (textContent !== prevValueRef.current) {
          isUserInputRef.current = true;
          prevValueRef.current = textContent;
        }
      });
    });

    return removeListener;
  }, [editor]);

  useEffect(() => {
    if (value !== prevValueRef.current || enableLineNumbers !== prevEnableLineNumbersRef.current) {
      // Skip reset if the change was triggered by user input (avoid cursor jump)
      if (isUserInputRef.current && enableLineNumbers === prevEnableLineNumbersRef.current) {
        prevValueRef.current = value;
        isUserInputRef.current = false;
        return;
      }
      // Update refs BEFORE editor.update to prevent re-entry
      prevValueRef.current = value;
      prevEnableLineNumbersRef.current = enableLineNumbers;
      isUserInputRef.current = false;

      queueMicrotask(() => {
        editor.update(() => {
          const root = $getRoot();
          root.clear();

          const parts = value.split(/(\{\{[^}]+\}\})/);

          if (enableLineNumbers) {
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
                const contextSuggestion = optionsRef.current.find(s => s.isContext && s.label === 'context');
                if (contextSuggestion) {
                  paragraph.append($createVariableNode(contextSuggestion));
                } else {
                  paragraph.append($createTextNode(part));
                }
                return
              }
              
              if (conversationMatch) {
                const [_, variableName] = conversationMatch;
                const conversationSuggestion = optionsRef.current.find(s => 
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

                const suggestion = optionsRef.current.find(s => {
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
        }, { tag: 'programmatic' });
      });
    } else {
      prevValueRef.current = value;
      prevEnableLineNumbersRef.current = enableLineNumbers;
      isUserInputRef.current = false;
    }
  }, [value, editor, enableLineNumbers]);

  return null;
};

export default InitialValuePlugin;