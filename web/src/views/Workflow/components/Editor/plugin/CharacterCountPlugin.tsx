import { useEffect } from 'react';
import { $getRoot, $isParagraphNode } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

import { $isVariableNode } from '../nodes/VariableNode';

const CharacterCountPlugin = ({ setCount, onChange }: { setCount: (count: number) => void; onChange?: (value: string) => void }) => {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const root = $getRoot();
        let serializedContent = '';
        
        // Traverse all nodes and serialize properly
        root.getChildren().forEach(child => {
          if ($isParagraphNode(child)) {
            child.getChildren().forEach(node => {
              if ($isVariableNode(node)) {
                serializedContent += node.getTextContent();
              } else {
                serializedContent += node.getTextContent();
              }
            });
          }
        });
        
        setCount(serializedContent.length);
        onChange?.(serializedContent);
      });
    });
  }, [editor, setCount, onChange]);

  return null;
}

export default CharacterCountPlugin