import { useEffect } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import {
  $createParagraphNode,
  $createTextNode,
  $getRoot,
  $getSelection,
  $setSelection,
  $createRangeSelection,
  $isTextNode,
  $isRangeSelection,
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
          const selection = $getSelection();
          if (!selection || !$isRangeSelection(selection)) return;
          
          const anchorNode = selection.anchor.getNode();
          const anchorOffset = selection.anchor.offset;
          
          if ($isTextNode(anchorNode)) {
            const nodeText = anchorNode.getTextContent();
            const textBeforeCursor = nodeText.substring(0, anchorOffset);
            const textAfterCursor = nodeText.substring(anchorOffset);
            
            // Find the last '/' position
            const lastSlashIndex = textBeforeCursor.lastIndexOf('/');
            
            if (lastSlashIndex !== -1) {
              // Split the text: before slash, insert variable, after cursor
              const beforeSlash = textBeforeCursor.substring(0, lastSlashIndex);
              
              // Update the current text node with text before slash
              anchorNode.setTextContent(beforeSlash);
              
              // Create and insert the variable node
              const tagNode = $createVariableNode(payload.data);
              const spaceNode = $createTextNode(' ');
              
              anchorNode.insertAfter(tagNode);
              tagNode.insertAfter(spaceNode);
              
              // Add remaining text if any
              if (textAfterCursor) {
                spaceNode.insertAfter($createTextNode(textAfterCursor));
              }
              
              // Set cursor after space
              const newSelection = $createRangeSelection();
              newSelection.anchor.set(spaceNode.getKey(), 1, 'text');
              newSelection.focus.set(spaceNode.getKey(), 1, 'text');
              $setSelection(newSelection);
            }
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