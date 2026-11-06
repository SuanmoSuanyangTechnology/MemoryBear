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
import { skills } from './zh/skills'

export const zh = {
  translation: {
    resourceSharing: {
      catalog: '共享到资源中心',
      editable: '允许复制为可编辑副本',
      market: '投稿到社区市场',
      description: '资源说明',
      descriptionRequired: '请输入资源说明',
      pendingSync: '共享设置已保存，社区市场同步待重试，请重新提交共享',
      failed: '共享失败，请重试',
    },
    resourceCenter: {
      taskInput: '任务输入', resultSummary: '结果汇总',
      multi_agent: '集群', examples: '使用示例', flowView: '流程视图', collaboration: '子智能体与协作方式',
      nodeCount: '{{count}} 个节点', agentCount: '{{count}} 个子智能体',
      editPermission: '编辑权限', enabled: '已开启', disabled: '已关闭',
      relatedSkills: '关联技能', relatedTools: '关联工具', updatedAt: '最后更新时间', updateNotes: '更新说明',
      supervisor: '主管模式', parallel: '并行执行', knowledgeAgent: '企业知识助手', salesAgent: '销售跟进助手', financeAgent: '财务分析助手',
      start: '开始', parse: '解析文档', check: '核对规范', risk: '整理风险与建议', approval: '生成通过说明', output: '输出审阅报告',
      supervisorHint: '并行执行', knowledgeAgentHint: '核对项目资料与制度要求', salesAgentHint: '评估客户价值与需求匹配', financeAgentHint: '分析预算与财务风险',
      startHint: '输入待审阅文档', parseHint: '提取正文与关键字段', checkHint: '对照企业制度与审阅要求', riskHint: '标记问题与修改依据', approvalHint: '概括文档亮点与核对结论', outputHint: '汇总审阅结果',
      zoomIn: '放大', zoomOut: '缩小', fit: '适应画布',
      title: '应用', subtitle: '租户内共享的应用', banner: 'Banner 占位图',
      search: '搜索应用名称、描述或发布人', all: '全部', knowledge: '知识问答',
      office: '办公效率', sales: '营销销售', writing: '内容创作',
      add: '添加到空间', detail: '应用详情', publisher: '发布人', version: '版本',
      type: '应用类型', views: '浏览次数', skills: '技能', tools: '工具',
      agent: '智能体', workflow: '工作流', space: '目标空间', selectSpace: '请选择目标空间',
      mockNotice: '当前为模拟数据，添加结果仅在本次页面会话内有效，不会创建真实应用。',
      noUpdateNotes: '暂无更新说明', added: '已添加到空间', duplicate: '该应用已添加到此空间，请选择其他空间。',
      failed: '操作失败，请重试', empty: '暂无符合条件的共享应用', more: '向下滚动加载更多',
    },
    ...home,
    ...account,
    ...model,
    ...knowledgeBase,
    ...application,
    ...userMemory,
    ...space,
    tool,
    skills,
    ...workflow,
    ...engine,
    ...detail,
    ...notificationCenter,
    ...memoryConversation,
  },
}
