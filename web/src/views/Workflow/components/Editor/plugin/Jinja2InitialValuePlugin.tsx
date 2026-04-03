/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-02 17:11:07 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-04-02 17:11:07 
 */
import { useEffect, useRef } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $createParagraphNode, $createTextNode } from 'lexical';

interface Jinja2InitialValuePluginProps {
  value: string;
}

const Jinja2InitialValuePlugin: React.FC<Jinja2InitialValuePluginProps> = ({ value }) => {
  const [editor] = useLexicalComposerContext();
  const prevValueRef = useRef<string>('');
  const isUserInputRef = useRef(false);

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState, tags }) => {
      if (tags.has('programmatic')) return;
      editorState.read(() => {
        const textContent = $getRoot().getTextContent();
        if (textContent !== prevValueRef.current) {
          isUserInputRef.current = true;
          prevValueRef.current = textContent;
        }
      });
    });
  }, [editor]);

  useEffect(() => {
    if (value === prevValueRef.current) return;

    if (isUserInputRef.current) {
      prevValueRef.current = value;
      isUserInputRef.current = false;
      return;
    }

    prevValueRef.current = value;
    isUserInputRef.current = false;

    queueMicrotask(() => {
      editor.update(() => {
        const root = $getRoot();
        root.clear();
        value.split('\n').forEach((line) => {
          const paragraph = $createParagraphNode();
          paragraph.append($createTextNode(line));
          root.append(paragraph);
        });
      }, { tag: 'programmatic' });
    });
  }, [value, editor]);

  return null;
};

export default Jinja2InitialValuePlugin;
