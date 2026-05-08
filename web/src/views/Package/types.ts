/*
 * @Author: ZhaoYing 
 * @Date: 2026-04-14 11:35:01 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-08 16:32:52
 */
export interface Package {
    id: string;
    // 名称
    name: string | null;
    name_en: string | null;
    // 类型
    category: "saas_personal" | "commercial_deployment";
    tier_level: number;
    // 版本
    version: string;
    // 状态
    status: boolean;
    // 价格
    price: string | null;
    // 计费周期
    billing_cycle: "monthly" | "yearly" | "permanent_free" | "local_deployment";
    // 核心价值
    core_value: string | null;
    core_value_en: string | null;
    // 技术支持
    tech_support: string | null;
    tech_support_en: string | null;
      // SLA与合规
    sla_compliance: string | null;
    sla_compliance_en: string | null;
    // 对话页面个性化配置
    page_customization: string | null;
    page_customization_en: string | null;
    // 主题色
    theme_color: string;
    quotas: {
      // API OPS 频次（次/秒）
      api_ops_rate_limit: number | null;
      // 空间数量
      workspace_quota: number | null;
      // 技能库数量
      skill_quota: number | null;
      // 应用数量
      app_quota: number | null;
      // 知识库容量
      knowledge_capacity_quota: number | null;
      // 记忆引擎数量
      memory_engine_quota: number | null;
      // 可记忆终端用户数
      end_user_quota: number | null;
      // 本体工程
      ontology_project_quota: number | null;
      // 可负载模型数量
      model_quota: number | null;
    },
    created_at: number;
    updated_at: number;
    created_by: string | null;
    updated_by: string | null;
    expired_at?: number | null;
}
