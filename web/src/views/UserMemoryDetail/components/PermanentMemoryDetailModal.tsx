import { forwardRef, useImperativeHandle, useState } from 'react'
import { Flex } from 'antd'
import { useTranslation } from 'react-i18next'
import RbModal from '@/components/RbModal'

export interface PermanentMemoryFact {
  id: string;
  title: string;
  content: string;
  scene?: string;
  colorIndex?: number;
}

export interface PermanentMemoryDetailModalRef {
  handleOpen: (fact: PermanentMemoryFact) => void;
}

/** Shows a permanent memory's title, scene and full content. */
const PermanentMemoryDetailModal = forwardRef<PermanentMemoryDetailModalRef>((_props, ref) => {
  const { t } = useTranslation()
  const [fact, setFact] = useState<PermanentMemoryFact | null>(null)

  useImperativeHandle(ref, () => ({ handleOpen: setFact }), [])

  return (
    <RbModal
      title={fact?.title}
      open={fact !== null}
      footer={null}
      onCancel={() => setFact(null)}
    >
      <Flex vertical gap={12}>
        <Flex gap={16} align="start">
          <span className="rb:shrink-0 rb:text-gray-600">{t('explicitDetail.scene')}</span>
          <span className="rb:min-w-0 rb:wrap-break-word">{fact?.scene || t('explicitDetail.defaultScene')}</span>
        </Flex>
        <div className="rb:rounded-xl rb:bg-gray-100 rb:p-3 rb:whitespace-pre-wrap rb:wrap-break-word rb:leading-6">
          {fact?.content}
        </div>
      </Flex>
    </RbModal>
  )
})

export default PermanentMemoryDetailModal
