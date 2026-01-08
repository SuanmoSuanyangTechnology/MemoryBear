import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Row, Col } from 'antd'

const GraphDetail: FC = () => {
  const { t } = useTranslation()

  return (
    <div className="rb:h-full rb:max-w-266 rb:mx-auto">
      GraphDetail
    </div>
  )
}
export default GraphDetail