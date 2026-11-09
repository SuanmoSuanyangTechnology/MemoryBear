import { $createParagraphNode, $createTextNode, type LexicalNode } from 'lexical';
import { ListNode, ListItemNode } from '@lexical/list';

const LIST_RULES = [
  { type: 'number', pattern: /^\d+\.\s+(.+)$/ },
  { type: 'bullet', pattern: /^[-*]\s+(.+)$/ },
] as const;

// 将 Markdown 文本解析为 Lexical 节点
export const parseMarkdownToNodes = (markdown: string): LexicalNode[] => {
  const lines = markdown.split('\n');
  const nodes: LexicalNode[] = [];
  
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    
    // 跳过空行
    if (!line) {
      i++;
      continue;
    }
    
    // 两类列表共用分组、行内解析和节点创建流程。
    const listRule = LIST_RULES.find(rule => rule.pattern.test(line));
    if (listRule) {
      const listNode = new ListNode(listRule.type, 1);
      while (i < lines.length) {
        const match = lines[i].trim().match(listRule.pattern);
        if (!match) {
          break;
        }
        const listItem = new ListItemNode();
        listItem.append(...parseInlineMarkdown(match[1]));
        listNode.append(listItem);
        i++;
      }
      nodes.push(listNode);
      continue;
    }
    
    // 普通段落，解析行内格式
    const paragraph = $createParagraphNode();
    const textNodes = parseInlineMarkdown(line);
    textNodes.forEach(node => paragraph.append(node));
    nodes.push(paragraph);
    i++;
  }
  
  return nodes;
};

// 解析行内 Markdown 格式（加粗、斜体、下划线）
const parseInlineMarkdown = (text: string): LexicalNode[] => {
  const nodes: LexicalNode[] = [];
  let remaining = text;
  
  while (remaining.length > 0) {
    // 匹配 **加粗**
    const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
    // 匹配 *斜体*
    const italicMatch = remaining.match(/\*(.+?)\*/);
    // 匹配 <u>下划线</u>
    const underlineMatch = remaining.match(/<u>(.+?)<\/u>/);
    
    // 找到最早的匹配
    let earliestMatch: { type: string; content: string; start: number; end: number } | null = null;
    
    if (boldMatch && boldMatch.index !== undefined) {
      earliestMatch = {
        type: 'bold',
        content: boldMatch[1],
        start: boldMatch.index,
        end: boldMatch.index + boldMatch[0].length
      };
    }
    
    if (italicMatch && italicMatch.index !== undefined) {
      if (!earliestMatch || italicMatch.index < earliestMatch.start) {
        earliestMatch = {
          type: 'italic',
          content: italicMatch[1],
          start: italicMatch.index,
          end: italicMatch.index + italicMatch[0].length
        };
      }
    }
    
    if (underlineMatch && underlineMatch.index !== undefined) {
      if (!earliestMatch || underlineMatch.index < earliestMatch.start) {
        earliestMatch = {
          type: 'underline',
          content: underlineMatch[1],
          start: underlineMatch.index,
          end: underlineMatch.index + underlineMatch[0].length
        };
      }
    }
    
    if (earliestMatch) {
      // 添加匹配前的普通文本
      if (earliestMatch.start > 0) {
        nodes.push($createTextNode(remaining.substring(0, earliestMatch.start)));
      }
      
      // 添加格式化文本
      const textNode = $createTextNode(earliestMatch.content);
      if (earliestMatch.type === 'bold') {
        textNode.setFormat(1); // Bold
      } else if (earliestMatch.type === 'italic') {
        textNode.setFormat(2); // Italic
      } else if (earliestMatch.type === 'underline') {
        textNode.setFormat(8); // Underline
      }
      nodes.push(textNode);
      
      // 继续处理剩余文本
      remaining = remaining.substring(earliestMatch.end);
    } else {
      // 没有更多匹配，添加剩余文本
      nodes.push($createTextNode(remaining));
      break;
    }
  }
  
  return nodes;
};

