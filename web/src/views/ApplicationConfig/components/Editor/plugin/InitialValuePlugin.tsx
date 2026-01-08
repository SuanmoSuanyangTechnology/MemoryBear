import { type FC, useEffect, useRef } from 'react';
import { $getRoot, $createParagraphNode, $createTextNode } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

// 设置初始值的插件
const InitialValuePlugin: FC<{ value?: string }> = ({ value }) => {
  const [editor] = useLexicalComposerContext();
  const lastValueRef = useRef<string | undefined>(undefined);
  
  useEffect(() => {
    // 只有当value真正发生变化时才更新
    if (lastValueRef.current !== value) {
      editor.update(() => {
        const root = $getRoot();
        const currentText = root.getTextContent();
        
        // 如果当前内容和新值相同，则不更新
        if (currentText === (value || '')) {
          return;
        }
        
        root.clear();
        if (value) {
          const paragraph = $createParagraphNode();
          const textNode = $createTextNode(value);
          paragraph.append(textNode);
          root.append(paragraph);
        } else {
          // 当value为undefined或空时，创建一个空段落
          const paragraph = $createParagraphNode();
          root.append(paragraph);
        }
      });
      lastValueRef.current = value;
    }
  }, [editor, value]);
  
  return null;
};

export default InitialValuePlugin