/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-23 16:22:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-02 17:14:15
 */
import { useEffect, useRef } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $createParagraphNode, $createTextNode } from 'lexical';

import { $createVariableNode } from '../nodes/VariableNode';
import { type Suggestion } from '../plugin/AutocompletePlugin'

interface InitialValuePluginProps {
  value: string;
  options?: Suggestion[];
}

const InitialValuePlugin: React.FC<InitialValuePluginProps> = ({ value, options = [] }) => {
  const [editor] = useLexicalComposerContext();
  const prevValueRef = useRef<string>('');
  const isUserInputRef = useRef(false);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState, tags }) => {
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
  }, [editor]);

  useEffect(() => {
    if (value !== prevValueRef.current) {
      if (isUserInputRef.current) {
        prevValueRef.current = value;
        isUserInputRef.current = false;
        return;
      }
      prevValueRef.current = value;
      isUserInputRef.current = false;

      queueMicrotask(() => {
        editor.update(() => {
          const root = $getRoot();
          root.clear();

          const parts = (value ?? '').split(/(\{\{[^}]+\}\}|\n)/);
          let paragraph = $createParagraphNode();

            parts.forEach(part => {
              if (part === '\n') {
                root.append(paragraph);
                paragraph = $createParagraphNode();
                return;
              }
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
                const fullValue = `conv.${variableName}`;
                // First try direct match on top-level label
                let conversationSuggestion = optionsRef.current.find(s =>
                  s.group === 'CONVERSATION' && s.label === variableName
                );
                // Then search children by value (e.g. conv.api_key.url)
                if (!conversationSuggestion) {
                  for (const s of optionsRef.current) {
                    if (s.group === 'CONVERSATION' && s.children) {
                      const child = s.children.find(c => c.value === fullValue);
                      if (child) { conversationSuggestion = child; break; }
                    }
                  }
                }
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
        }, { tag: 'programmatic' });
      });
    }
  }, [value, editor]);

  return null;
};

export default InitialValuePlugin;