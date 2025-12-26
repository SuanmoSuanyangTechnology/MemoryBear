import { useEffect } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import {
  $createParagraphNode,
  $createTextNode,
  $getRoot,
  $setSelection,
  $createRangeSelection,
  $isParagraphNode,
  $isTextNode,
} from 'lexical';

import { $createVariableNode } from '../nodes/VariableNode';
import {
  INSERT_VARIABLE_COMMAND,
  CLEAR_EDITOR_COMMAND,
  FOCUS_EDITOR_COMMAND,
  type InsertVariableCommandPayload,
} from '../commands';

const CommandPlugin = () => {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    const unregisterInsertVariable = editor.registerCommand(
      INSERT_VARIABLE_COMMAND,
      (payload: InsertVariableCommandPayload) => {
        editor.update(() => {
          const root = $getRoot();
          const text = root.getTextContent();
          const lastSlashIndex = text.lastIndexOf('/');
          
          // Find the paragraph and the position to insert
          const paragraph = root.getFirstChild();
          if (!paragraph || !$isParagraphNode(paragraph)) return;
          
          const children = paragraph.getChildren();
          let insertPosition = 0;
          let currentTextLength = 0;
          
          // Find where to insert the new tag
          for (let i = 0; i < children.length; i++) {
            const child = children[i];
            const childText = child.getTextContent();
            
            if (currentTextLength + childText.length > lastSlashIndex) {
              // Split this text node if needed
              if ($isTextNode(child)) {
                const beforeSlash = childText.substring(0, lastSlashIndex - currentTextLength);
                const afterSlash = childText.substring(lastSlashIndex - currentTextLength + 1);
                
                if (beforeSlash) {
                  child.setTextContent(beforeSlash);
                  insertPosition = i + 1;
                } else {
                  insertPosition = i;
                  child.remove();
                }
                
                // Insert tag and space
                const tagNode = $createVariableNode(payload.data);
                const spaceNode = $createTextNode(' ');
                
                if (insertPosition < paragraph.getChildrenSize()) {
                  paragraph.getChildAtIndex(insertPosition)?.insertBefore(tagNode);
                  tagNode.insertAfter(spaceNode);
                } else {
                  paragraph.append(tagNode);
                  paragraph.append(spaceNode);
                }
                
                if (afterSlash) {
                  spaceNode.insertAfter($createTextNode(afterSlash));
                }
                
                // Set cursor after space
                const selection = $createRangeSelection();
                selection.anchor.set(spaceNode.getKey(), 1, 'text');
                selection.focus.set(spaceNode.getKey(), 1, 'text');
                $setSelection(selection);
              }
              break;
            }
            
            currentTextLength += childText.length;
            insertPosition = i + 1;
          }
        });
        return true;
      },
      1
    );

    const unregisterClearEditor = editor.registerCommand(
      CLEAR_EDITOR_COMMAND,
      () => {
        editor.update(() => {
          const root = $getRoot();
          root.clear();
          const paragraph = $createParagraphNode();
          root.append(paragraph);
        });
        return true;
      },
      1
    );

    const unregisterFocusEditor = editor.registerCommand(
      FOCUS_EDITOR_COMMAND,
      () => {
        editor.focus();
        return true;
      },
      1
    );

    return () => {
      unregisterInsertVariable();
      unregisterClearEditor();
      unregisterFocusEditor();
    };
  }, [editor]);

  return null;
};

export default CommandPlugin;