import { type FC, type ComponentType, type SVGProps } from 'react';
import Icon from '@ant-design/icons'

import SpaceSvg from '@/assets/images/package/space.svg?react'
import SkillSvg from '@/assets/images/package/skill.svg?react'
import AppSvg from '@/assets/images/package/app.svg?react'
import KnowledgeSvg from '@/assets/images/package/knowledge.svg?react'
import MemoryConfigSvg from '@/assets/images/package/memory_config.svg?react'
import EndUserSvg from '@/assets/images/package/end_user.svg?react'
import OntologySvg from '@/assets/images/package/ontology.svg?react'
import ModelSvg from '@/assets/images/package/model.svg?react'
import TechnicalSupportSvg from '@/assets/images/package/technical_support.svg?react'
import ApiOpsSvg from '@/assets/images/package/api_ops.svg?react'
import slaSvg from '@/assets/images/package/sla.svg?react';
import MemoryWriteQpsSvg from '@/assets/images/package/memory_write_qps.svg?react'
import UserMemoryLimitSvg from '@/assets/images/package/memory_limit.svg?react'

const iconMap: Record<string, ComponentType<SVGProps<SVGSVGElement>>> = {
  space: SpaceSvg,
  skill: SkillSvg,
  app: AppSvg,
  knowledge: KnowledgeSvg,
  memory_config: MemoryConfigSvg,
  end_user: EndUserSvg,
  ontology: OntologySvg,
  model: ModelSvg,
  technical_support: TechnicalSupportSvg,
  api_ops: ApiOpsSvg,
  sla: slaSvg,
  memory_write_qps: MemoryWriteQpsSvg,
  user_memory_limit: UserMemoryLimitSvg,
}

const PackageIcon: FC<{ iconKey: string; color?: string }> = ({ iconKey, color = '#171719' }) => {
    const SvgComponent = iconMap[iconKey]
    if (!SvgComponent) return null
    return <Icon component={SvgComponent} style={{ color, fontSize: 16 }} />
}
export default PackageIcon