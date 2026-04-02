/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-23 16:22:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-02 17:13:45
 */
import { useEffect, useRef } from 'react';
import { $getRoot, $isParagraphNode } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

import { $isVariableNode } from '../nodes/VariableNode';

const serialize = (root: ReturnType<typeof $getRoot>): string => {
  const paragraphs: string[] = [];
  root.getChildren().forEach(child => {
    if ($isParagraphNode(child)) {
      let content = '';
      child.getChildren().forEach(node => {
        content += $isVariableNode(node) ? node.getTextContent() : node.getTextContent();
      });
      paragraphs.push(content);
    }
  });
  return paragraphs.join('\n');
};

const CharacterCountPlugin = ({
  setCount,
  onChange,
  waitForInit = false,
}: {
  setCount: (count: number) => void;
  onChange?: (value: string) => void;
  waitForInit?: boolean;
}) => {
  const [editor] = useLexicalComposerContext();
  // lastProgrammaticValue tracks what InitialValuePlugin wrote, so we can
  // suppress onChange when the content hasn't actually changed from that value.
  const lastProgrammaticValueRef = useRef<string | null>(null);
  const isReadyRef = useRef(!waitForInit);
  const isFirstUpdateRef = useRef(true);

  useEffect(() => {
    return editor.registerUpdateListener(({ editorState, tags }) => {
      if (tags.has('programmatic')) {
        isReadyRef.current = true;
        isFirstUpdateRef.current = false;
        editorState.read(() => {
          lastProgrammaticValueRef.current = serialize($getRoot());
        });
        return;
      }
      if (!isReadyRef.current) return;
      editorState.read(() => {
        const content = serialize($getRoot());
        // Skip the first update if content is empty (editor initial render)
        if (isFirstUpdateRef.current) {
          isFirstUpdateRef.current = false;
          if (content === '') return;
        }
        // Skip if content is identical to what was programmatically written
        if (content === lastProgrammaticValueRef.current) return;
        lastProgrammaticValueRef.current = null;
        setCount(content.length);
        onChange?.(content);
      });
    });
  }, [editor, setCount, onChange]);

  return null;
};

export default CharacterCountPlugin;
