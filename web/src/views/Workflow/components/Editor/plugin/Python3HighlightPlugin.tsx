import { useEffect, useRef } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { TextNode, $createTextNode, $getSelection, $isRangeSelection, COMMAND_PRIORITY_LOW, PASTE_COMMAND } from 'lexical';

const PYTHON_KEYWORDS = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue',
  'def', 'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import',
  'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while',
  'with', 'yield'
]);

const Python3HighlightPlugin = () => {
  const [editor] = useLexicalComposerContext();
  const isPastingRef = useRef(false);

  useEffect(() => {
    return editor.registerCommand(
      PASTE_COMMAND,
      () => {
        isPastingRef.current = true;
        setTimeout(() => {
          isPastingRef.current = false;
        }, 100);
        return false;
      },
      COMMAND_PRIORITY_LOW
    );
  }, [editor]);

  useEffect(() => {
    return editor.registerNodeTransform(TextNode, (textNode: TextNode) => {
      if (isPastingRef.current) return;
      
      const text = textNode.getTextContent();
      
      if (textNode.hasFormat('code')) return;
      if (textNode.getStyle()) return;
      if (!needsHighlight(text)) return;
      
      const parent = textNode.getParent();
      if (!parent) return;

      const selection = $getSelection();
      let selectionOffset = null;
      if ($isRangeSelection(selection)) {
        const anchor = selection.anchor;
        if (anchor.getNode() === textNode) {
          selectionOffset = anchor.offset;
        }
      }

      const tokens = tokenizePython(text);
      if (tokens.length <= 1) return;
      
      const newNodes = tokens.map(token => {
        const newNode = $createTextNode(token.text);
        newNode.toggleFormat('code');
        
        switch (token.type) {
          case 'keyword':
            newNode.setStyle('color: #d73a49; font-weight: 600;');
            break;
          case 'string':
            newNode.setStyle('color: #032f62;');
            break;
          case 'comment':
            newNode.setStyle('color: #6a737d; font-style: italic;');
            break;
          case 'number':
            newNode.setStyle('color: #005cc5; font-weight: 500;');
            break;
          case 'function':
            newNode.setStyle('color: #6f42c1; font-weight: 500;');
            break;
        }
        
        return newNode;
      });

      if (newNodes.length > 1) {
        textNode.replace(newNodes[0]);
        for (let i = 1; i < newNodes.length; i++) {
          newNodes[i - 1].insertAfter(newNodes[i]);
        }
        
        if (selectionOffset !== null && $isRangeSelection(selection)) {
          let currentOffset = 0;
          for (const node of newNodes) {
            const nodeLength = node.getTextContent().length;
            if (currentOffset + nodeLength >= selectionOffset) {
              node.select(selectionOffset - currentOffset, selectionOffset - currentOffset);
              break;
            }
            currentOffset += nodeLength;
          }
        }
      }
    });
  }, [editor]);

  return null;
};

function needsHighlight(text: string): boolean {
  return /[a-zA-Z0-9_#"']/.test(text);
}

function tokenizePython(text: string): Array<{text: string, type: string}> {
  const tokens: Array<{text: string, type: string}> = [];
  let i = 0;
  
  while (i < text.length) {
    // Comments
    if (text[i] === '#') {
      let start = i;
      while (i < text.length && text[i] !== '\n') i++;
      tokens.push({ text: text.slice(start, i), type: 'comment' });
      continue;
    }
    
    // Strings
    if (text[i] === '"' || text[i] === "'") {
      const quote = text[i];
      let start = i++;
      const isTriple = text.slice(start, start + 3) === quote.repeat(3);
      if (isTriple) i += 2;
      
      while (i < text.length) {
        if (isTriple && text.slice(i, i + 3) === quote.repeat(3)) {
          i += 3;
          break;
        } else if (!isTriple && text[i] === quote && text[i - 1] !== '\\') {
          i++;
          break;
        }
        i++;
      }
      tokens.push({ text: text.slice(start, i), type: 'string' });
      continue;
    }
    
    // Numbers
    if (/\d/.test(text[i])) {
      let start = i;
      while (i < text.length && /[\d.]/.test(text[i])) i++;
      tokens.push({ text: text.slice(start, i), type: 'number' });
      continue;
    }
    
    // Keywords and identifiers
    if (/[a-zA-Z_]/.test(text[i])) {
      let start = i;
      while (i < text.length && /[a-zA-Z0-9_]/.test(text[i])) i++;
      const word = text.slice(start, i);
      
      if (PYTHON_KEYWORDS.has(word)) {
        tokens.push({ text: word, type: 'keyword' });
      } else if (i < text.length && text[i] === '(') {
        tokens.push({ text: word, type: 'function' });
      } else {
        tokens.push({ text: word, type: 'text' });
      }
      continue;
    }
    
    // Other characters
    let start = i;
    while (i < text.length && !/[a-zA-Z0-9_#"']/.test(text[i])) i++;
    if (start < i) {
      tokens.push({ text: text.slice(start, i), type: 'text' });
    }
  }
  
  return tokens;
}

export default Python3HighlightPlugin;
