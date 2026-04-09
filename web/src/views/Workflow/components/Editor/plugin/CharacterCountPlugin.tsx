import { useEffect } from 'react';
import { $getRoot, $isParagraphNode } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

const CharacterCountPlugin = ({ setCount }: { setCount: (count: number) => void }) => {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState, tags }) => {
      if (tags.has('programmatic')) return;
      editorState.read(() => {
        const root = $getRoot();
        const paragraphs: string[] = [];
        root.getChildren().forEach(child => {
          if ($isParagraphNode(child)) {
            paragraphs.push(child.getChildren().map(n => n.getTextContent()).join(''));
          }
        });
        setCount(paragraphs.join('\n').length);
      });
    });
  }, [editor, setCount]);

  return null;
}

export default CharacterCountPlugin