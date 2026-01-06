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
        const paragraphs: string[] = [];
        root.getChildren().forEach(child => {
          if ($isParagraphNode(child)) {
            let paragraphContent = '';
            child.getChildren().forEach(node => {
              if ($isVariableNode(node)) {
                paragraphContent += node.getTextContent();
              } else {
                paragraphContent += node.getTextContent();
              }
            });
            paragraphs.push(paragraphContent);
          }
        });
        
        serializedContent = paragraphs.join('\n');
        
        setCount(serializedContent.length);
        onChange?.(serializedContent);
      });
    });
  }, [editor, setCount, onChange]);

  return null;
}

export default CharacterCountPlugin