/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-02 17:11:07 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-04-02 17:11:07 
 */
import { useEffect, useRef } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { $getRoot, $createParagraphNode, $createTextNode, $isParagraphNode } from 'lexical';

interface Jinja2InitialValuePluginProps {
  value?: string;
  onChange?: (value: string) => void;
}

const Jinja2InitialValuePlugin: React.FC<Jinja2InitialValuePluginProps> = ({ value, onChange }) => {
  const [editor] = useLexicalComposerContext();
  const internalValueRef = useRef<string | undefined>(undefined);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState, tags }) => {
      if (tags.has('programmatic')) return;
      if (internalValueRef.current === undefined) return;
      editorState.read(() => {
        const paragraphs = $getRoot().getChildren()
          .filter($isParagraphNode)
          .map(p => p.getChildren().map(n => n.getTextContent()).join(''));
        const text = paragraphs.join('\n');
        if (text !== internalValueRef.current) {
          internalValueRef.current = text;
          onChangeRef.current?.(text);
        }
      });
    });
  }, [editor]);

  useEffect(() => {
    if (value === undefined) return;
    if (value === internalValueRef.current) return;

    internalValueRef.current = value;
    editor.update(() => {
      const root = $getRoot();
      root.clear();
      value.split('\n').forEach((line) => {
        const paragraph = $createParagraphNode();
        paragraph.append($createTextNode(line));
        root.append(paragraph);
      });
    }, { tag: 'programmatic' });
  }, [value, editor]);

  return null;
};

export default Jinja2InitialValuePlugin;
