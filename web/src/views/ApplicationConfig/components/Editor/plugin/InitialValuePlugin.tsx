/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:24:59 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:24:59 
 */
/**
 * Initial Value Plugin
 * Sets the initial content of the Lexical editor
 * Only updates when the value prop changes
 */

import { type FC, useEffect, useRef } from 'react';
import { $getRoot, $createParagraphNode, $createTextNode } from 'lexical';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';

/**
 * Plugin to set initial editor value
 */
const InitialValuePlugin: FC<{ value?: string }> = ({ value }) => {
  const [editor] = useLexicalComposerContext();
  const lastValueRef = useRef<string | undefined>(undefined);
  
  useEffect(() => {
    // Only update when value actually changes
    if (lastValueRef.current !== value) {
      editor.update(() => {
        const root = $getRoot();
        const currentText = root.getTextContent();
        
        // If current content matches new value, don't update
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
          // When value is undefined or empty, create an empty paragraph
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