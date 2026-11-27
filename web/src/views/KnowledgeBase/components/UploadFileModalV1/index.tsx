/**
 * "Upload File" modal (V1)
 *
 * Replicates the prototype's interaction and layout: the parent selects files via antd Upload then calls the ref to open the modal,
 * which is a three-column parsing config: left "Upload Document Confirmation" file list, middle "Parsing Config" vertical navigation
 * (Document parsing / Chunking strategy / Multimodal processing / Advanced settings), right config items and bottom action buttons.
 *
 * State logic lives in useUploadFileModal, the presentation is split into three panels; config items reuse CreateKnowledgeBaseModal's Section.
 */
import { forwardRef, useImperativeHandle } from 'react';
import { Form } from 'antd';

import RbModal from '@/components/RbModal';
import ConfigContentPanel from './components/ConfigContentPanel';
import ConfigNavPanel from './components/ConfigNavPanel';
import FileListPanel from './components/FileListPanel';
import useUploadFileModal from './useUploadFileModal';
import type { UploadFileModalV1Props, UploadFileModalV1Ref } from './types';

const UploadFileModalV1 = forwardRef<UploadFileModalV1Ref, UploadFileModalV1Props>((props, ref) => {
  const {
    form,
    visible,
    loading,
    activeSection,
    setActiveSection,
    fileList,
    isRechunkMode,
    isParentChildMode,
    customModels,
    handleOpen,
    handleAddFiles,
    handleRechunk,
    handleClose,
    handleRemove,
    handleConfirm,
  } = useUploadFileModal(props);

  useImperativeHandle(ref, () => ({ handleOpen, handleAddFiles, handleRechunk }));

  return (
    <RbModal
      open={visible}
      onCancel={handleClose}
      width={1080}
      closable={false}
      footer={null}
      needWrapper={false}
      classNames={{ content: 'rb:p-0! rb:overflow-hidden!' }}
    >
      <Form
        form={form}
        labelAlign="left"
        labelCol={{ span: 12 }}
        labelWrap
        className="rb:flex rb:h-[calc(100vh-64px)] rb:overflow-hidden!"
      >
        <FileListPanel
          fileList={fileList}
          isRechunkMode={isRechunkMode}
          onAddFiles={() => props.onRequestFiles?.()}
          onRemove={handleRemove}
        />
        <ConfigNavPanel activeSection={activeSection} onChange={setActiveSection} />
        <ConfigContentPanel
          activeSection={activeSection}
          customModels={customModels}
          hasFiles={fileList.length > 0}
          isRechunkMode={isRechunkMode}
          isParentChildMode={isParentChildMode}
          loading={loading}
          onClose={handleClose}
          onConfirm={handleConfirm}
        />
      </Form>
    </RbModal>
  );
});

export default UploadFileModalV1;
export type { UploadFileModalV1Props, UploadFileModalV1Ref } from './types';
