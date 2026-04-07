import { useEffect } from 'react';
import { $getRoot, $isParagraphNode } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

const Jinjia2CharacterCountPlugin = ({ setCount }: { setCount: (count: number) => void }) => {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const root = $getRoot();
        const paragraphs = root.getChildren()
          .filter($isParagraphNode)
          .map(p => p.getChildren().map(n => n.getTextContent()).join(''));
        setCount(paragraphs.join('\n').length);
      });
    });
  }, [editor, setCount]);

  return null;
}

export default Jinjia2CharacterCountPlugin