import { request } from "@/utils/request";
// List query parameters
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
    codeName: string;
  };
  introduction_en?: {
    releaseDate: string;
    upgradePosition: string;
    coreUpgrades: string[];
    codeName: string;
  };
}
// Dashboard data statistics
export const getDashboardData = `/home-page/workspaces`

// Dashboard statistics
export const getDashboardStatistics = async () => {
    const response = await request.get(`/home-page/statistics`);
    return response as DataResponse;
};
// Get version
export const getVersion = async () => {
  const response = await request.get(`/home-page/version`);
  return response as versionResponse;
};