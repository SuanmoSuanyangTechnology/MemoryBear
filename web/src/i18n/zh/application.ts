import { applicationPart1 } from './applicationPart1'
import { applicationPart2 } from './applicationPart2'

export const application = {
  application: {
    ...applicationPart1,
    ...applicationPart2,
  },
}
