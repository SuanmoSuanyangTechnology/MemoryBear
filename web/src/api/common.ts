import { request } from "@/utils/request";
// 列表查询参数
export interface Query {
  page?: number;
  pagesize?: number;
  orderby?: string;
  desc?: boolean;
  keywords?: string;
  [key: string]: unknown;
}
export interface DataResponse {
  total_models: Number;
  total_llm: Number;
  total_embedding: Number;
  model_week_growth_rate: Number;
  active_workspaces: Number;
  new_workspaces_this_week: Number;
  workspace_week_growth_rate: Number;
  total_users: Number;
  new_users_this_week: Number;
  user_week_growth_rate: Number;
  running_apps: Number;
  new_apps_this_week: Number;
  app_week_growth_rate: Number
}
export interface versionResponse{
  version: string;
  introduction: {
    releaseDate: string;
    upgradePosition: string;
    coreUpgrades: string[];
  };
}
// 首页数据统计
export const getDashboardData = `/home-page/workspaces`

// 首页数据看板统计
export const getDashboardStatistics = async () => {
    const response = await request.get(`/home-page/statistics`);
    return response as DataResponse;
};
// 获取版本号
export const getVersion = async () => {
  const response = await request.get(`/home-page/version`);
  return response as versionResponse;
};