import { workflowPart1 } from './workflowPart1'
import { workflowPart2 } from './workflowPart2'

export const workflow = {
  workflow: {
    ...workflowPart1,
    ...workflowPart2,
  },
}
