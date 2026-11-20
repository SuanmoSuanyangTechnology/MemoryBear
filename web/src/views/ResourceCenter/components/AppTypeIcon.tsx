import { type FC, useEffect, useState } from 'react'
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
  const [iconError, setIconError] = useState(false)

  useEffect(() => {
    setIconError(false)
  }, [icon, source_app_type])

  const displayIcon = icon && !iconError ? icon : typeIcons[source_app_type as string]

  return (
    <img
      src={displayIcon}
      alt=""
      className={clsx("rb:size-12 rb:shrink-0 rb:rounded-lg", className)}
      onError={() => !iconError && setIconError(true)}
    />
  )
}

export default AppTypeIcon
