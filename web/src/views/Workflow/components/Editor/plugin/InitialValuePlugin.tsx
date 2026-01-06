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
  const initializedRef = useRef(false);

  useEffect(() => {
    if (!initializedRef.current && value) {
      editor.update(() => {
        const root = $getRoot();
        root.clear();
        const paragraph = $createParagraphNode();

        const parts = value.split(/(\{\{[^}]+\}\})/);

        parts.forEach(part => {
          const match = part.match(/^\{\{([^.]+)\.([^}]+)\}\}$/);
          const contextMatch = part.match(/^\{\{context\}\}$/);
          const conversationMatch = part.match(/^\{\{conv\.([^}]+)\}\}$/);

          // 匹配{{context}}格式
          if (contextMatch) {
            const contextSuggestion = options.find(s => s.isContext && s.label === 'context');
            if (contextSuggestion) {
              paragraph.append($createVariableNode(contextSuggestion));
            } else {
              paragraph.append($createTextNode(part));
            }
            return
          }
          
          // 匹配{{conv.xx}}格式
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
          
          // 匹配普通变量{{nodeId.label}}格式
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
      });
      
      initializedRef.current = true;
    }
  }, [options]);

  return null;
};

export default InitialValuePlugin;