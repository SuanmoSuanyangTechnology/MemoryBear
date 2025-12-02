# i18n 中英文对比报告

## 📊 统计概览

- **中文键总数**: 1136
- **英文键总数**: 1052
- **中文缺失**: 27 个键
- **英文缺失**: 111 个键

---

## ❌ 英文缺失的翻译（111个）

### 1. Application 模块 (3个)
- `application.cluster` - 集群
- `application.clusterDesc` - 创建Agent集群
- `application.fullAmount` - 全量

### 2. Role 角色管理模块 (15个)
- `role.roleManagement` - 角色管理
- `role.roleId` - 角色ID
- `role.roleName` - 角色名称
- `role.roleCode` - 角色编码
- `role.description` - 角色描述
- `role.status` - 状态
- `role.enabled` - 已启用
- `role.disabled` - 已停用
- `role.createTime` - 创建时间
- `role.createRole` - 新建角色
- `role.editRole` - 编辑角色
- `role.roleTemplate` - 角色模板
- `role.emptyTemplate` - 空模板
- `role.adminTemplate` - 管理员模板
- `role.userTemplate` - 用户模板
- `role.confirmDelete` - 确定要删除这个角色吗？
- `role.createSuccess` - 角色创建成功
- `role.updateSuccess` - 角色更新成功
- `role.deleteSuccess` - 角色删除成功
- `role.createFailed` - 角色创建失败
- `role.updateFailed` - 角色更新失败
- `role.deleteFailed` - 角色删除失败

### 3. Tenant 租户管理模块 (20个)
- `tenant.tenantId` - 租户ID
- `tenant.tenantName` - 租户名称
- `tenant.contactPerson` - 联系人
- `tenant.contactInfo` - 联系方式
- `tenant.status` - 状态
- `tenant.enabled` - 启用
- `tenant.disabled` - 禁用
- `tenant.expiryDate` - 到期时间
- `tenant.createTenant` - 新增租户
- `tenant.editTenant` - 编辑租户
- `tenant.searchPlaceholder` - 搜索租户ID、名称、联系人或联系方式
- `tenant.confirmDelete` - 确定要删除该租户吗？
- `tenant.confirmBatchDelete` - 确定要批量删除选中的租户吗？
- `tenant.fetchFailed` - 获取租户数据失败
- `tenant.batchEnableSuccess` - 批量启用成功
- `tenant.batchEnableFailed` - 批量启用失败
- `tenant.batchDisableSuccess` - 批量停用成功
- `tenant.batchDisableFailed` - 批量停用失败
- `tenant.exportSuccess` - 导出成功
- `tenant.batchDeleteSuccess` - 批量删除成功
- `tenant.batchDeleteFailed` - 批量删除失败
- `tenant.saveFailed` - 保存失败
- `tenant.batchImport` - 批量导入

### 4. User 用户管理模块 (13个)
- `user.tenantName` - 所属租户
- `user.password` - 密码
- `user.expiryDate` - 有效期
- `user.expiryDateDue` - 有效期至
- `user.batchImport` - 批量导入
- `user.batchImportUser` - 批量导入用户
- `user.downloadTemplate` - 下载导入模板
- `user.templateDownloadSuccess` - 模板下载成功
- `user.startImport` - 开始导入
- `user.batchImportSuccess` - 批量导入成功
- `user.importFailed` - 导入失败，请检查文件格式
- `user.noFileSelected` - 请选择要导入的文件
- `user.onlyXlsxOrCsv` - 只能上传 .xlsx 或 .csv 格式的文件
- `user.reselect` - 重新选择
- `user.noFileSelectedTip` - 未选择任何文件
- `user.downloadTemplateTip` - 请下载模板，填写用户信息后上传。

### 5. Product 产品管理模块 (13个)
- `product.applicationManagement` - 应用管理
- `product.createApplication` - 创建应用
- `product.applicationName` - 应用名称
- `product.applicationIcon` - 应用图标
- `product.applicationNameRequired` - 请输入应用名称
- `product.associationStatus` - 关联状态
- `product.associated` - 已关联
- `product.notAssociated` - 未关联
- `product.unassociate` - 解除关联
- `product.unassociateSuccess` - 解除关联成功
- `product.unassociateFailed` - 解除关联失败
- `product.viewKey` - 查看KEY
- `product.viewStats` - 查看统计
- `product.disableSuccess` - 停用成功
- `product.enableSuccess` - 启用成功
- `product.operationFailed` - 操作失败

### 6. 其他模块 (47个)
- `count` - 计数: {{count}}
- `increment` - 增加
- `decrement` - 减少
- `reset` - 重置
- `switchLanguage` - 切换语言
- `home.title` - 首页
- `home.welcome` - 欢迎使用我们的带单页路由的 React 应用！
- `home.counterCard` - 计数器演示
- `home.aboutCard` - 关于我们
- `home.workflowCard` - 工作流编辑器
- `home.websocketDemoCard` - WebSocket 演示
- `home.sseDemoCard` - SSE演示
- `workflow.title` - 工作流编辑器
- `workflow.description` - 拖拽节点创建连接，构建您的工作流程。点击节点可进行配置。
- `workflow.addNode` - 添加节点
- `workflow.deleteNode` - 删除选中
- `workflow.saveWorkflow` - 保存工作流
- `workflow.startNode` - 触发节点
- `workflow.conditionNode` - 条件判断
- `workflow.actionNode` - 执行动作
- `workflow.endNode` - 结束节点
- `workflow.newNode` - 新节点
- `workflow.node` - 节点
- `workflow.nodesCreated` - 已创建节点
- `workflow.loadingNodes` - 正在加载节点 {{progress}}%
- `workflow.loadingFailed` - 加载节点失败
- `workflow.create5kNodes` - 创建5000节点
- `workflow.create10kNodes` - 创建10000节点
- `notFound.title` - 页面未找到
- `notFound.description` - 请求的页面不存在。
- `notFound.backToHome` - 返回首页

---

## ✅ 中文缺失的翻译（27个）

### 1. Common 通用模块 (1个)
- `common.operateSuccess` - Operation successful

### 2. KnowledgeBase 知识库模块 (3个)
- `knowledgeBase.models` - Model
- `knowledgeBase.owner` - Owner
- `knowledgeBase.operation` - Operation

### 3. Application 应用模块 (15个)
- `application.multi_agent` - Cluster
- `application.multi_agentDesc` - Create an Agent Cluster
- `application.current` - Current
- `application.versionName` - Version Name
- `application.versionNameTip` - Version number format: v[major version number].[next version number].[revision number] (e.g. v1.3.0)
- `application.agentName` - Agent Name
- `application.roleType` - Role Type
- `application.coordinator` - Coordinator
- `application.analyzer` - Analyzer
- `application.executor` - Executor
- `application.reviewer` - Reviewer
- `application.updateSubAgent` - Update Sub Agent
- `application.subAgentMaxLength` - Sub Agent maximum {{maxLength}}
- `application.capabilities` - Capabilities

### 4. Space 空间模块 (5个)
- `space.storageType` - Storage Type
- `space.rag` - RAG storage
- `space.ragDesc` - Based on vector retrieval, suitable for document Q&A and semantic search
- `space.neo4j` - Graph storage
- `space.neo4jDesc` - Based on knowledge graph, suitable for relational reasoning and path query

### 5. MemoryExtractionEngine 记忆提取引擎模块 (4个)
- `memoryExtractionEngine.coreEntitiesAfterDedup` - Core entities after deduplication
- `memoryExtractionEngine.extractRelationalTriples` - Extracted relational triples (partial)
- `memoryExtractionEngine.extractRelationalTriplesDesc` - There are a total of {{count}} segments with clear semantic boundaries
- `memoryExtractionEngine.theEffectOfEntityDisambiguationLLMDriven` - The effect of entity disambiguation (LLM driven)

---

## 🎯 建议

### 优先级 1 - 核心功能模块（需要立即补充）
1. **Role 角色管理** - 完整模块缺失（15个键）
2. **Tenant 租户管理** - 完整模块缺失（20个键）
3. **Product 产品管理** - 完整模块缺失（13个键）
4. **User 用户管理扩展** - 批量导入功能缺失（13个键）

### 优先级 2 - 功能增强（建议补充）
1. **Application 应用模块** - 多代理相关功能（15个键）
2. **Space 空间模块** - 存储类型配置（5个键）
3. **MemoryExtractionEngine** - 实体去重相关（4个键）

### 优先级 3 - 演示/测试功能（可选）
1. **Home/Workflow/NotFound** - 演示页面（30个键）
2. **通用计数器功能** - 测试功能（5个键）

---

## 📝 下一步行动

1. **补充英文翻译**: 优先补充 Role、Tenant、Product、User 模块的英文翻译
2. **补充中文翻译**: 补充 Application、Space、MemoryExtractionEngine 模块的中文翻译
3. **清理无用翻译**: 如果 Home/Workflow 等演示功能不再使用，可以考虑从中文文件中移除
4. **建立翻译规范**: 建议建立翻译键的命名规范和审查流程，避免未来出现遗漏

