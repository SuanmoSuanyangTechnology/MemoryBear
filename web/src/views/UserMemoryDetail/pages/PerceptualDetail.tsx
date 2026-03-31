/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-08 19:46:02 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 11:13:19
 */
import { type FC } from 'react'
import { Row, Col } from 'antd'

import PerceptualLastInfo from '../components/PerceptualLastInfo'
import Timeline from '../components/Timeline'

/**
 * PerceptualDetail – Two-column view of a user's perceptual memory.
 *
 * Left column (fixed 480px): real-time sensory dashboard showing the latest
 * visual, auditory and text perception streams (PerceptualLastInfo).
 * Right column (fluid): chronological perception timeline (Timeline).
 *
 * Route param `id` (consumed by child components) identifies the end-user.
 */
const PerceptualDetail: FC = () => {

  return (
    <Row gutter={12} className="rb:h-full!">
      <Col flex="480px" className="rb:h-full!">
        <PerceptualLastInfo />
      </Col>
      <Col flex="1" className="rb:h-full!">
        <Timeline />
      </Col>
    </Row>
  )
}
export default PerceptualDetail