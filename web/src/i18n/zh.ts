import { home } from './zh/home'
import { account } from './zh/account'
import { model } from './zh/model'
import { knowledgeBase } from './zh/knowledgeBase'
import { application } from './zh/application'
import { userMemory } from './zh/userMemory'
import { space } from './zh/space'
import { tool } from './zh/tool'
import { workflow } from './zh/workflow'
import { engine } from './zh/engine'
import { detail } from './zh/detail'
import { notificationCenter } from './zh/notificationCenter'
import { memoryConversation } from './zh/memoryConversation'

export const zh = {
  translation: {
    ...home,
    ...account,
    ...model,
    ...knowledgeBase,
    ...application,
    ...userMemory,
    ...space,
    ...tool,
    ...workflow,
    ...engine,
    ...detail,
    ...notificationCenter,
    ...memoryConversation,
  },
}
