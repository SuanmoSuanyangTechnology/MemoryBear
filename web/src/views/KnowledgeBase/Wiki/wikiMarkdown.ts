interface MarkdownNode {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
}

/** Transform text nodes only: code, existing links and raw HTML remain inert. */
export function remarkWikiLinks() {
  return (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode) => {
      if (!node.children || ['link', 'image', 'code', 'inlineCode', 'html'].includes(node.type)) return;
      // Markdown emphasis may split a valid Wiki label across several inline nodes.
      const joined: MarkdownNode[] = [];
      for (let index = 0; index < node.children.length; index++) {
        const child = node.children[index];
        const opening = child.type === 'text' ? [...(child.value ?? '').matchAll(/\[\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\|/gi)]
          .find(match => !(child.value ?? '').slice(match.index! + match[0].length).includes(']]')) : undefined;
        if (!opening) { joined.push(child); continue; }
        const label: MarkdownNode[] = [];
        const start = child.value!.slice(opening.index! + opening[0].length);
        if (start) label.push({ type: 'text', value: start });
        let end = index + 1;
        let suffix = '';
        let closed = false;
        for (; end < node.children.length; end++) {
          const part = node.children[end];
          if (['link', 'image', 'html', 'break'].includes(part.type) || part.value?.includes('\n')) break;
          const closing = part.type === 'text' ? part.value?.indexOf(']]') ?? -1 : -1;
          if (closing >= 0) {
            if (closing) label.push({ type: 'text', value: part.value!.slice(0, closing) });
            suffix = part.value!.slice(closing + 2);
            closed = true;
            break;
          }
          label.push(part);
        }
        if (!closed || start.includes('\n')) { joined.push(child); continue; }
        const prefix = child.value!.slice(0, opening.index);
        if (prefix) joined.push({ type: 'text', value: prefix });
        joined.push({ type: 'link', url: `/wiki-page/${opening[1]}`, children: label });
        if (suffix) node.children[end] = { type: 'text', value: suffix };
        index = suffix ? end - 1 : end;
      }
      node.children = joined;
      node.children = node.children.flatMap(child => {
        if (child.type !== 'text' || !child.value) {
          visit(child);
          return [child];
        }
        const pattern = /\[\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\|([^\]\n]+)\]\]|\[\^(R\d+)\]/gi;
        const nodes: MarkdownNode[] = [];
        let offset = 0;
        for (const match of child.value.matchAll(pattern)) {
          const index = match.index!;
          if (index > offset) nodes.push({ type: 'text', value: child.value.slice(offset, index) });
          const ref = match[3]?.toUpperCase();
          nodes.push({
            type: 'link', url: ref ? `#wiki-source-${ref}` : `/wiki-page/${match[1]}`,
            children: [{ type: 'text', value: ref ? `[${ref}]` : match[2] }],
          });
          offset = index + match[0].length;
        }
        if (!nodes.length) return [child];
        if (offset < child.value.length) nodes.push({ type: 'text', value: child.value.slice(offset) });
        return nodes;
      });
    };
    visit(tree);
  };
}
