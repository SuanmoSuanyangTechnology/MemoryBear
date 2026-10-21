import { useEffect, useRef, useState, type FC, type Key, type ReactNode } from 'react';
import { Tree, Skeleton } from 'antd';
import type { DataNode } from 'antd/es/tree';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

import { useWikiTree } from './hooks/useWikiTree';
import SearchInput from '@/components/SearchInput';
import Empty from '@/components/Empty';
import switcherIcon from '@/assets/images/knowledgeBase/switcher.png';

import {
  WIKI_CATALOG_PAGE_ID,
  WIKI_SUMMARY_PAGE_ID,
  type WikiPage,
} from './Wiki/types';

interface PrivateDirectoryPanelProps {
  directoryTab: 'files' | 'wiki';
  wikiActive: boolean;
  pages: WikiPage[];
  knowledgeBaseId: string;
  selectedPageId?: string;
  fileDirectory: ReactNode;
  onPageSelect: (pageId: string) => void;
}

// Each visible branch has its own pagination sentinel.
const BranchLoader = ({ folderId, load, label, failed }: {
  folderId: string;
  load: (folderId: string, more: boolean) => Promise<void>;
  label: string;
  failed: boolean;
}) => {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element || failed) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) void load(folderId, true);
    }, { root: element.closest('[data-wiki-scroll]'), rootMargin: '80px' });
    observer.observe(element);
    return () => observer.disconnect();
  }, [folderId, load, failed]);
  return <span ref={ref} className="rb:text-[12px] rb:text-gray-600" onClick={() => void load(folderId, true)}>{label}</span>;
};

const PrivateDirectoryPanel: FC<PrivateDirectoryPanelProps> = ({
  directoryTab,
  wikiActive,
  pages,
  knowledgeBaseId,
  selectedPageId,
  fileDirectory,
  onPageSelect,
}) => {
  const { t } = useTranslation();
  const [keyword, setKeyword] = useState('');
  const directoryRef = useRef<HTMLDivElement>(null);
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([]);
  const { branches, load } = useWikiTree(knowledgeBaseId, wikiActive);

  useEffect(() => {
    setExpandedKeys([]);
  }, [knowledgeBaseId, wikiActive]);
  const loading = !branches[''] || (branches[''].loading && !branches[''].items.length);
  const isFilesMode = directoryTab === 'files';
  const buildBranch = (folderId = '', ancestors = new Set<string>()): DataNode[] => {
    if (ancestors.has(folderId)) return [];
    const branch = branches[folderId];
    if (!branch) return [];
    const nextAncestors = new Set(ancestors).add(folderId);
    const nodes: DataNode[] = branch.items.map((item) => {
      const folder = item.node_kind === 'folder';
      return {
        key: folder ? `folder:${item.id}` : item.id,
        title: item.title,
        selectable: !folder,
        isLeaf: !folder,
        children: folder && branches[item.id]
          ? buildBranch(item.id, nextAncestors)
          : undefined,
      };
    });
    if (branch.hasNext || branch.error) {
      nodes.push({
        key: `more:${folderId}:${branch.page}:${branch.loading}`,
        selectable: false,
        isLeaf: true,
        title: branch.loading ? t('common.loading') : (
          <BranchLoader
            folderId={folderId}
            load={load}
            failed={branch.error}
            label={branch.error ? t('knowledgeBase.wikiLoadFailed') : t('common.loading')}
          />
        ),
      });
    }
    return nodes;
  };
  const hasWikiTreeData = Boolean(branches['']?.items.length);
  const treeData: DataNode[] = [
    { key: WIKI_CATALOG_PAGE_ID, title: t('knowledgeBase.wikiCatalog'), isLeaf: true },
    ...(hasWikiTreeData ? [
      { key: WIKI_SUMMARY_PAGE_ID, title: t('knowledgeBase.wikiSummary'), isLeaf: true },
      ...(keyword.trim() ? pages.filter((page) =>
        `${page.title} ${page.summary} ${page.category}`.toLocaleLowerCase().includes(keyword.trim().toLocaleLowerCase()),
      ).map((page) => ({ key: page.id, title: page.title, isLeaf: true })) : buildBranch()),
    ] : []),
  ];

  useEffect(() => {
    if (!wikiActive) return;
    const path = pages.find((item) => item.id === selectedPageId)?.folderPath ?? [];
    setExpandedKeys((keys) => [...new Set([...keys, ...path.map((folder) => `folder:${folder.id}`)])]);
    let active = true;
    const reveal = async () => {
      const ids = ['', ...path.map((folder) => folder.id)];
      for (const id of ids) {
        if (!active) return;
        const branch = branches[id];
        if (!branch) {
          await load(id);
          return;
        }
        const target = id === (path.at(-1)?.id ?? '') ? selectedPageId : path[ids.indexOf(id)]?.id;
        if (!branch.items.some((item) => item.id === target) && branch.hasNext && !branch.error) {
          await load(id, true);
          return;
        }
      }
    };
    if (selectedPageId && selectedPageId !== WIKI_CATALOG_PAGE_ID && selectedPageId !== WIKI_SUMMARY_PAGE_ID) void reveal();
    return () => { active = false; };
  }, [pages, selectedPageId, branches, load, wikiActive]);
  useEffect(() => {
    if (isFilesMode || loading || !selectedPageId) return;
    const container = directoryRef.current;
    const tree = container?.querySelector<HTMLElement>('.wiki-directory-tree');
    if (!container || !tree) return;

    const scrollSelectedIntoView = () => {
      const title = Array.from(
        container?.querySelectorAll<HTMLElement>(
          '.wiki-directory-tree [data-wiki-page-id]',
        ) ?? [],
      ).find((element) => element.dataset.wikiPageId === selectedPageId);
      const selected = title?.closest<HTMLElement>('.ant-tree-treenode');
      if (!selected) return;
      const containerBounds = container.getBoundingClientRect();
      const selectedBounds = selected.getBoundingClientRect();
      const visibleTop = containerBounds.top + container.clientTop + 8;
      const visibleBottom = containerBounds.top + container.clientTop + container.clientHeight - 8;
      if (selectedBounds.top < visibleTop) {
        container.scrollTop += selectedBounds.top - visibleTop;
      } else if (selectedBounds.bottom > visibleBottom) {
        container.scrollTop += selectedBounds.bottom - visibleBottom;
      }
    };
    let frame = 0;
    const scheduleScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(scrollSelectedIntoView);
    };
    const mutationObserver = new MutationObserver(scheduleScroll);
    mutationObserver.observe(tree, { childList: true, subtree: true, attributes: true });
    const resizeObserver = new ResizeObserver(scheduleScroll);
    resizeObserver.observe(container);
    resizeObserver.observe(tree);
    scheduleScroll();

    return () => {
      cancelAnimationFrame(frame);
      mutationObserver.disconnect();
      resizeObserver.disconnect();
    };
  }, [selectedPageId, expandedKeys, isFilesMode, loading, keyword]);

  return (
    <aside className="rb:flex rb:h-full rb:w-63 rb:p-3 rb:shrink-0 rb:flex-col rb:border-r rb:border-[#EAECEE] rb:bg-[#FBFCFD]">
      {!isFilesMode && (
        <SearchInput
          defaultValue={keyword}
          debounceDelay={300}
          variant="outlined"
          className="rb:w-full!"
          placeholder={t('knowledgeBase.wikiSearch')}
          onSearch={setKeyword}
        />
      )}

      <div data-wiki-scroll ref={directoryRef} className="rb:min-h-0 rb:flex-1 rb:overflow-y-auto">
        <div className={clsx({ 'rb:hidden': !isFilesMode })}>
          {fileDirectory}
        </div>

        {!isFilesMode &&
          (loading ? (
            <Skeleton active />
          ) : treeData.length ? (
            <Tree.DirectoryTree
              virtual={false}
              motion={null}
              showIcon={false}
              expandedKeys={expandedKeys}
              onExpand={(keys, { expanded, node }) => {
                setExpandedKeys(keys);
                if (expanded) void load(String(node.key).slice('folder:'.length));
              }}
              selectedKeys={selectedPageId ? [selectedPageId] : []}
              treeData={treeData}
              titleRender={(node) => (
                <span data-wiki-page-id={node.isLeaf ? String(node.key) : undefined}>
                  {node.title as ReactNode}
                </span>
              )}
              switcherIcon={<img src={switcherIcon} className="rb:size-4" />}
              className="wiki-directory-tree customTree rb:bg-transparent! rb:mt-3!"
              onSelect={(keys) => {
                const pageId = keys[0];
                if (pageId) onPageSelect(String(pageId));
              }}
            />
          ) : (
            <Empty
              size={88}
              subTitle={t('knowledgeBase.wikiEmptyTitle')}
              className="rb:h-full!"
            />
          ))}
      </div>
    </aside>
  );
};

export default PrivateDirectoryPanel;
