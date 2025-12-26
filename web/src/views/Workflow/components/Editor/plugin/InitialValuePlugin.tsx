import { useEffect, useRef } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $createParagraphNode, $createTextNode } from 'lexical';

import { $createVariableNode } from '../nodes/VariableNode';
import { type Suggestion } from '../plugin/AutocompletePlugin'

interface InitialValuePluginProps {
  value: string;
  suggestions?: Suggestion[];
}

const InitialValuePlugin: React.FC<InitialValuePluginProps> = ({ value, suggestions = [] }) => {
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
          if (match) {
            const [, nodeId, label] = match;
            const suggestion = suggestions.find(s => s.nodeData.id === nodeId && s.label === label);
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
  }, []);

  return null;
};

export default InitialValuePlugin;