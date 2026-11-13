import { type FC } from 'react'
import clsx from 'clsx'

import { typeIcons } from '@/views/ApplicationManagement/constants'

interface AppTypeIconProps {
  icon: string | null | undefined
  source_app_type: string
  className?: string
}

const AppTypeIcon: FC<AppTypeIconProps> = ({
  icon,
  source_app_type,
  className,
}) => {
  return <img src={icon || typeIcons[source_app_type as string]} alt="" className={clsx("rb:size-12 rb:shrink-0 rb:rounded-lg", className)} />
}

export default AppTypeIcon
