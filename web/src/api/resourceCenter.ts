import { request } from "@/utils/request";
import type {
  AppAssetsQuery,
  AppAssetsResponse,
  AppAssetDetail,
  Categories,
} from '@/views/ResourceCenter/types';

// Resource Center · App List
export const getAppAssetsUrl = '/apps/assets'
export const getAppAssets = (data?: AppAssetsQuery) => {
  return request.get<AppAssetsResponse>(getAppAssetsUrl, data)
};

// Resource Center · App Detail
export const getAppAssetDetail = (asset_id: string) => {
  return request.get<AppAssetDetail>(`/apps/assets/${asset_id}`)
};

// Resource Center · Install App to My Workspace
export const appAssetInstall = (asset_id: string, data: { target_workspace_id: string; }) => {
  return request.post<AppAssetDetail>(`/apps/assets/${asset_id}/install`, data)
};

// Resource Center · Unshare App
export const deleteAppAssetInstall = (asset_id: string) => {
  return request.delete<AppAssetDetail>(`/apps/assets/${asset_id}`)
};

// Resource Center · Feature Categories
export const getAppAssetsCategoriesUrl = '/apps/assets/categories'
export const getAppAssetsCategories = () => {
  return request.get<Categories[]>(getAppAssetsCategoriesUrl)
};
