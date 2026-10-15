import { useCallback, useState, type CSSProperties } from 'react';
import { Button, Spin } from 'antd';
import { DownOutlined, RightOutlined } from '@ant-design/icons';
import { getWikiTree, type WikiTreeNode } from '@/api/wiki';
import { useWikiResource } from './useWikiResource';

interface Props { kbId: string; selectedId?: string; onSelect: (id: string) => void; folderId?: string; depth?: number; types?: string }

function Folder({ node, depth, ...props }: Props & { node: WikiTreeNode; depth: number }) {
  const [open, setOpen] = useState(depth === 0);
  return (
    <li className="wiki-tree-folder">
      <button className="wiki-tree-folder-button" aria-expanded={open} onClick={() => setOpen(value => !value)}>
        {open ? <DownOutlined /> : <RightOutlined />}<span>{node.title}</span>
      </button>
      {open ? <WikiTree {...props} folderId={node.id} depth={depth + 1} /> : null}
    </li>
  );
}

export default function WikiTree({ kbId, folderId, selectedId, onSelect, depth = 0, types }: Props) {
  const [cursor, setCursor] = useState<string>();
  const [previous, setPrevious] = useState<WikiTreeNode[]>([]);
  const load = useCallback((signal: AbortSignal) => getWikiTree(kbId, folderId, cursor, signal, types), [kbId, folderId, cursor, types]);
  const { data, loading, error, reload } = useWikiResource(load);
  const nodes = [...previous, ...(data?.items ?? [])];
  const seen = new Set<string>();
  return (
    <ul className="wiki-tree" style={{ '--wiki-tree-depth': depth } as CSSProperties} aria-label={depth === 0 ? 'Wiki 页面目录' : '子目录'}>
      {nodes.filter(node => !seen.has(node.id) && seen.add(node.id)).map(node => node.node_kind === 'folder'
        ? <Folder key={node.id} node={node} kbId={kbId} selectedId={selectedId} onSelect={onSelect} depth={depth} types={types} />
        : <li key={node.id}><button className={`wiki-tree-page ${selectedId === node.id ? 'is-selected' : ''}`}
            aria-current={selectedId === node.id ? 'page' : undefined} onClick={() => onSelect(node.id)} title={node.title}>
            {node.title}
          </button></li>)}
      {loading ? <li className="wiki-tree-feedback"><Spin size="small" /></li> : null}
      {error ? <li className="wiki-tree-feedback"><span>目录加载失败</span><Button type="link" size="small" onClick={reload}>重试</Button></li> : null}
      {!loading && !error && nodes.length === 0 && depth === 0 ? <li className="wiki-tree-feedback">暂无可读页面</li> : null}
      {data?.next_cursor ? <li><Button type="link" size="small" onClick={() => {
        setPrevious(nodes);
        setCursor(data.next_cursor!);
      }}>加载更多</Button></li> : null}
    </ul>
  );
}
