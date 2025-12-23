import { useEffect } from 'react';
import { $getRoot } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

const CharacterCountPlugin = ({ setCount, onChange }: { setCount: (count: number) => void; onChange?: (value: string) => void }) => {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        const root = $getRoot();
        const textContent = root.getTextContent();
        setCount(textContent.length);
        onChange?.(textContent);
      });
    });
  }, [editor, setCount, onChange]);

  return null;
}

export default CharacterCountPlugin