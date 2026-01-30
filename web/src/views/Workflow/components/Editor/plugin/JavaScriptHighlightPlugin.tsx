import { useEffect } from 'react';
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext';
import { TextNode, $createTextNode, $getSelection, $isRangeSelection } from 'lexical';

const JS_KEYWORDS = new Set([
  'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger', 'default',
  'delete', 'do', 'else', 'export', 'extends', 'finally', 'for', 'function', 'if', 'import',
  'in', 'instanceof', 'let', 'new', 'return', 'super', 'switch', 'this', 'throw', 'try',
  'typeof', 'var', 'void', 'while', 'with', 'yield', 'true', 'false', 'null', 'undefined'
]);

const JavaScriptHighlightPlugin = () => {
  const [editor] = useLexicalComposerContext();

  useEffect(() => {
    return editor.registerNodeTransform(TextNode, (textNode: TextNode) => {
      const text = textNode.getTextContent();
      
      if (textNode.hasFormat('code')) return;
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

      const tokens = tokenizeJavaScript(text);
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
  return /[a-zA-Z0-9_/"'`]/.test(text);
}

function tokenizeJavaScript(text: string): Array<{text: string, type: string}> {
  const tokens: Array<{text: string, type: string}> = [];
  let i = 0;
  
  while (i < text.length) {
    // Single-line comments
    if (text.slice(i, i + 2) === '//') {
      let start = i;
      while (i < text.length && text[i] !== '\n') i++;
      tokens.push({ text: text.slice(start, i), type: 'comment' });
      continue;
    }
    
    // Multi-line comments
    if (text.slice(i, i + 2) === '/*') {
      let start = i;
      i += 2;
      while (i < text.length && text.slice(i, i + 2) !== '*/') i++;
      if (i < text.length) i += 2;
      tokens.push({ text: text.slice(start, i), type: 'comment' });
      continue;
    }
    
    // Strings
    if (text[i] === '"' || text[i] === "'" || text[i] === '`') {
      const quote = text[i];
      let start = i++;
      
      while (i < text.length) {
        if (text[i] === quote && text[i - 1] !== '\\') {
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
    if (/[a-zA-Z_$]/.test(text[i])) {
      let start = i;
      while (i < text.length && /[a-zA-Z0-9_$]/.test(text[i])) i++;
      const word = text.slice(start, i);
      
      if (JS_KEYWORDS.has(word)) {
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
    while (i < text.length && !/[a-zA-Z0-9_$/"'`]/.test(text[i])) i++;
    if (start < i) {
      tokens.push({ text: text.slice(start, i), type: 'text' });
    }
  }
  
  return tokens;
}

export default JavaScriptHighlightPlugin;
