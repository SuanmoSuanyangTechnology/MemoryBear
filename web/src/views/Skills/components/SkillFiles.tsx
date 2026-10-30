import { useTranslation } from 'react-i18next';
import { useState, type FC } from 'react';
import { Tree, Flex } from 'antd';
import type { DataNode } from 'antd/es/tree';
import clsx from 'clsx'

import Empty from '@/components/Empty';
import './SkillImport.css';

export type PreviewFile = { path: string; content?: string | null; is_binary?: boolean };

function getFolderKeys(files: PreviewFile[]): string[] {
  const folders = new Set<string>();
  for (const { path } of files) {
    const parts = path.split('/');
    for (let index = 1; index < parts.length; index++) {
      folders.add(parts.slice(0, index).join('/'));
    }
  }
  return [...folders];
}

const SkillFiles: FC<{ files: PreviewFile[] }> = ({ files }) => {
  const { t } = useTranslation();
  const [activeFile, setActiveFile] = useState(files[0]?.path ?? '');
  const [expandedKeys, setExpandedKeys] = useState<string[]>(() => getFolderKeys(files));
  const selected = files.find(item => item.path === activeFile) ?? files[0];
  if (!files.length) return <Empty size={88} subTitle={t('skills.noFiles')} className="rb-border rb:rounded-xl" />;

  const treeData: DataNode[] = [];
  const folders = new Map<string, DataNode>();
  for (const file of files) {
    const parts = file.path.split('/');
    let children = treeData;
    let prefix = '';
    for (const folder of parts.slice(0, -1)) {
      prefix = prefix ? `${prefix}/${folder}` : folder;
      let node = folders.get(prefix);
      if (!node) {
        node = { key: prefix, title: folder, selectable: false, children: [] };
        children.push(node);
        folders.set(prefix, node);
      }
      children = node.children!;
    }
    children.push({
      key: file.path,
      title: parts[parts.length - 1],
      isLeaf: true,
      className: 'rb:group',
      icon: props => <div className={clsx("rb:size-4 rb:bg-cover rb:group-hover:bg-[url('@/assets/images/application/text.svg')]", {
        "rb:bg-[url('@/assets/images/application/text_white.svg')]": props.selected,
        "rb:bg-[url('@/assets/images/application/text.svg')]": !props.selected
      })} />,
    });
  }

  return (
    <Flex aria-label={t('skills.filePreview')} component="section" className="rb:h-96 rb:overflow-hidden rb:rounded-xl rb-border">
      <nav aria-label={t('skills.directoryTree')} className="rb:w-50 rb:shrink-0 rb:overflow-auto rb-border-r rb:bg-slate-50 rb:p-2">
        <Tree blockNode showIcon className="skill-import-tree" treeData={treeData}
          selectedKeys={[selected.path]} expandedKeys={expandedKeys} autoExpandParent={false}
          onExpand={keys => setExpandedKeys(keys.map(String))}
          onSelect={keys => { if (keys.length) setActiveFile(String(keys[0])); }} />
      </nav>
      <Flex vertical className="rb:min-w-0 rb:flex-1">
        <div className="rb-border-b rb:p-3 rb:text-gray-600">{selected.path}</div>
        <pre aria-label={t('skills.fileContent')} tabIndex={0} className="rb:m-0 rb:flex-1 rb:overflow-auto rb:whitespace-pre-wrap rb:bg-gray-100 rb:p-4 rb:text-[12px]">
          {selected.is_binary || selected.content == null ? t('skills.noTextPreview') : selected.content}
        </pre>
      </Flex>
    </Flex>
  );
}
export default SkillFiles