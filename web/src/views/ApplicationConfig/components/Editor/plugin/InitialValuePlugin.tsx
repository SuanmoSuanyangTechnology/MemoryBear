import { type FC, useEffect } from 'react';
import { $getRoot, $createParagraphNode, $createTextNode } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

// 设置初始值的插件
const InitialValuePlugin: FC<{ value?: string }> = ({ value }) => {
  const [editor] = useLexicalComposerContext();
  
  useEffect(() => {
    if (value) {
      editor.update(() => {
        const root = $getRoot();
        root.clear();
        const paragraph = $createParagraphNode();
        const textNode = $createTextNode(value);
        paragraph.append(textNode);
        root.append(paragraph);
      });
    }
  }, [editor, value]);
  
  return null;
};

export default InitialValuePlugin