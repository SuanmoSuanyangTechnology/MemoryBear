import { useEffect } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $createParagraphNode, $createTextNode } from 'lexical';

interface InitialValuePluginProps {
  value: string;
}

const InitialValuePlugin: React.FC<InitialValuePluginProps> = ({ value }) => {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    if (value) {
      editor.update(() => {
        const root = $getRoot();
        if (root.getTextContent() === '') {
          root.clear();
          const paragraph = $createParagraphNode();
          paragraph.append($createTextNode(value));
          root.append(paragraph);
        }
      });
    }
  }, [editor, value]);

  return null;
};

export default InitialValuePlugin;