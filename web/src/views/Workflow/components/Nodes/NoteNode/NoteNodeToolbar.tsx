import { type FC } from 'react';
import { Flex, Dropdown, type MenuProps, Switch, Button, Divider } from 'antd';
import { UnorderedListOutlined, BoldOutlined, ItalicOutlined, StrikethroughOutlined, LinkOutlined, DashOutlined } from '@ant-design/icons';
import { Node } from '@antv/x6';
import { useTranslation } from 'react-i18next'

import { THEME_MAP } from '../../../constant';
const FONT_SIZES = [
  { label: '小', value: 12 },
  { label: '中', value: 14 },
  { label: '大', value: 16 },
];

interface NoteNodeToolbarProps {
  node: Node;
  onFormat: (type: string, value?: unknown) => void;
  toolConfig: Record<string, number | boolean>;
  nodeId: string;
}

const NoteNodeToolbar: FC<NoteNodeToolbarProps> = ({ node, onFormat, toolConfig, nodeId }) => {
  const data = node?.getData() || {};
  const { t } = useTranslation();

  const colorItems: MenuProps['items'] = Object.entries(THEME_MAP).map(([key, theme]) => ({
    key,
    label: (
      <div
        className="rb:w-5 rb:h-5 rb:rounded-full rb:cursor-pointer rb:border rb:border-gray-200"
        style={{ background: theme.bg }}
        onClick={() => onFormat('color', key)}
      />
    ),
  }));

  const fontSizeItems: MenuProps['items'] = FONT_SIZES.map(({ label, value }) => ({
    key: value,
    label: <span onClick={() => onFormat('fontSize', value)}>{label}</span>,
  }));

  const currentFontSize = FONT_SIZES.find(f => f.value === toolConfig.fontSize)?.label ?? '小';

  const handleClick: MenuProps['onClick'] = (e) => {
    switch (e.key) {
      case 'delete':
        node.remove()
        break;
      case 'copy':
        break;
    }
  }
  const handleChange = (type: string) => {
    let show_author = data.config.show_author.defaultValue
    if(type === 'showAuth'){
      show_author = !show_author
    }
    node.setData({
      ...data,
      config: {
        ...data.config,
        show_author: {
          ...data.config.show_author,
          defaultValue: show_author
        }
      }
    })
  }

  return (
    <Flex
      align="center"
      gap={8}
      className="rb:absolute rb:-top-11 rb:left-1/2 rb:-translate-x-1/2 rb:bg-white rb:z-10 rb:whitespace-nowrap rb:rounded-lg rb:py-1! rb:px-3!"
      onClick={e => e.stopPropagation()}
    >
      {/* Color picker */}
      <Dropdown menu={{ items: colorItems }} trigger={['click']}>
        <div
          className="rb:w-5 rb:h-5 rb:rounded-full rb:cursor-pointer rb:border rb:border-gray-200"
          style={{ background: THEME_MAP[data.bgColor]?.bg || THEME_MAP.blue.bg }}
        />
      </Dropdown>

      <Divider type="vertical" />

      {/* Font size */}
      <Dropdown menu={{ items: fontSizeItems }} trigger={['click']}>
        <Flex align="center" gap={4} className="rb:cursor-pointer rb:text-xs rb:text-gray-600 rb:select-none">
          <span className="rb:text-xs">Aa</span>
          <span className="rb:text-xs">{currentFontSize}</span>
        </Flex>
      </Dropdown>

      <Divider type="vertical" />

      {/* Bold */}
      <Button
        type={toolConfig.bold ? 'primary' : 'text'}
        icon={<BoldOutlined />}
        onClick={() => onFormat('bold')}
      />

      {/* Italic */}
      <Button
        type={toolConfig.italic ? 'primary' : 'text'}
        icon={<ItalicOutlined />}
        onClick={() => onFormat('italic')}
      />

      {/* Strikethrough */}
      <Button
        type={toolConfig.strikethrough ? 'primary' : 'text'}
        icon={<StrikethroughOutlined />}
        onClick={() => onFormat('strikethrough')}
      />

      {/* Link */}
      <Button
        type={toolConfig.link ? 'primary' : 'text'}
        icon={<LinkOutlined />}
        onClick={() => {
          const sel = window.getSelection();
          const rect = sel && sel.rangeCount > 0 ? sel.getRangeAt(0).getBoundingClientRect() : undefined;
          window.dispatchEvent(new CustomEvent('note:edit-link', { detail: { id: nodeId, url: '', rect } }));
        }}
      />

      {/* List */}
      <Button
        type={toolConfig.list ? 'primary' : 'text'}
        icon={<UnorderedListOutlined />}
        onClick={() => onFormat('list')}
      />

      <Divider type="vertical" />

      <Dropdown
        menu={{
          items: [
            // { key: 'copy', label: t('common.copy') },
            {
              key: 'showAuth',
              label: <Flex align="center" gap={24}>
                {t('workflow.config.notes.showAuth')}
                <Switch
                  size="small"
                  checked={data.config.show_author.defaultValue}
                  onChange={() => handleChange('showAuth')}
                />
              </Flex>
            },
            { key: 'delete', label: <Flex>{t('common.delete')}</Flex> },
          ],
          onClick: handleClick
        }}
      >
        <DashOutlined />
      </Dropdown>
    </Flex>
  );
};

export default NoteNodeToolbar;
