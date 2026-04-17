/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-14 11:35:01 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-14 14:28:46
 */
export interface Package {
  id: string;
  // 名称
  name: string;
  name_en: string;
  // 类型
  category: "saas_personal" | "commercial_deployment";
  tier_level: number;
  // 版本
  version: string;
  // 状态
  status: boolean;
  // 价格
  price: string;
  // 计费周期
  billing_cycle: "monthly" | "yearly" | "permanent_free" | "local_deployment";
  // 核心价值
  core_value: string;
  core_value_en: string;
  // 技术支持
  tech_support: string;
  tech_support_en: string;
  // SLA与合规
  sla_compliance: string;
  sla_compliance_en: string;
  // 对话页面个性化配置
  page_customization: string;
  page_customization_en: string;
  // API OPS 频次（次/秒）
  api_ops_rate_limit: number;
  // 主题色
  theme_color: string;
  quotas: {
    // 空间数量
    workspace_quota: number;
    // 技能库数量
    skill_quota: number;
    // 应用数量
    app_quota: number;
    // 知识库容量
    knowledge_capacity_quota: string;
    // 记忆引擎数量
    memory_engine_quota: number;
    // 可记忆终端用户数
    end_user_quota: number;
    // 本体工程
    ontology_project_quota: number;
    // 可负载模型数量
    model_quota: number;
  },
  created_at: number;
  updated_at: number;
  created_by: string;
  updated_by: string | null;
}
