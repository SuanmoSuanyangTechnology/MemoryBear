import { type FC, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Flex, Button, Input } from 'antd';
import { EditOutlined, DisconnectOutlined } from '@ant-design/icons';

const POPOVER_STYLE: React.CSSProperties = {
  position: 'fixed',
  zIndex: 1000,
  background: '#fff',
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  boxShadow: '0 2px 8px rgba(0,0,0,0.12)',
  whiteSpace: 'nowrap',
};

interface LinkPopoverProps {
  url: string;
  rect: DOMRect;
  onEdit: () => void;
  onRemove: () => void;
}

export const LinkPopover: FC<LinkPopoverProps> = ({ url, rect, onEdit, onRemove }) => {
  const { t } = useTranslation();
  return createPortal(
    <div
      style={{ ...POPOVER_STYLE, left: rect.left, top: rect.bottom + 4, padding: '4px 10px', fontSize: 12 }}
      onMouseDown={e => e.stopPropagation()}
    >
      <Flex align="center" gap={8}>
        <a href={url} target="_blank" rel="noreferrer"
          className="rb:text-[#2563eb] rb:max-w-40 rb:overflow-hidden rb:inline-block rb:text-ellipsis"
        >
          {url}
        </a>
        <Button size="small" type="text" icon={<EditOutlined />} onClick={onEdit}>{t('common.edit')}</Button>
        <Button size="small" type="text" icon={<DisconnectOutlined />} onClick={onRemove}>{t('workflow.config.notes.removeLink')}</Button>
      </Flex>
    </div>,
    document.body
  );
};

interface EditLinkPopoverProps {
  rect: DOMRect;
  initialUrl: string;
  onConfirm: (url: string) => void;
  onClose: () => void;
}

export const EditLinkPopover: FC<EditLinkPopoverProps> = ({ rect, initialUrl, onConfirm, onClose }) => {
  const { t } = useTranslation();
  const [url, setUrl] = useState(initialUrl);
  const confirm = () => {
    onClose();
    onConfirm(url);
  };
  return createPortal(
    <div
      style={{ ...POPOVER_STYLE, left: rect.left, top: rect.bottom + 4, padding: '8px' }}
      onMouseDown={e => e.stopPropagation()}
    >
      <Flex gap={8}>
        <Input
          size="small"
          className="rb:w-60!"
          placeholder={t('workflow.config.notes.enterLink')}
          value={url}
          onChange={e => setUrl(e.target.value)}
          onKeyDown={e => {
            e.stopPropagation();
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              e.preventDefault();
              confirm();
            }
          }}
          autoFocus
        />
        <Button size="small" type="primary" onClick={confirm}>{t('common.confirm')}</Button>
      </Flex>
    </div>,
    document.body
  );
};
