import { lazy, type LazyExoticComponent, type ComponentType, type ReactNode } from 'react';
import { createHashRouter, createRoutesFromElements, Route } from 'react-router-dom';

// 导入路由配置JSON
import routesConfig from './routes.json';


// 递归函数，用于生成路由元素

// 递归收集所有路由中的element
function collectElements(routes: RouteConfig[]): Set<string> {
  const elements = new Set<string>();

  function traverse(routeList: RouteConfig[]) {
    routeList.forEach(route => {
      // 添加当前路由的element
      elements.add(route.element);

      // 递归处理子路由
      if (route.children && route.children.length > 0) {
        traverse(route.children);
      }
    });
  }

  traverse(routes);
  return elements;
}

// 直接定义组件映射表，避免动态路径解析问题
const componentMap: Record<string, LazyExoticComponent<ComponentType<object>>> = {
  // 布局组件
  AuthLayout: lazy(() => import('@/components/Layout/AuthLayout')),
  AuthSpaceLayout: lazy(() => import('@/components/Layout/AuthSpaceLayout')),
  BasicLayout: lazy(() => import('@/components/Layout/BasicLayout')),
  LoginLayout: lazy(() => import('@/components/Layout/LoginLayout')),
  // 视图组件
  Home: lazy(() => import('@/views/Home')),
  UserMemory: lazy(() => import('@/views/UserMemory')),
  UserMemoryDetail: lazy(() => import('@/views/UserMemoryDetail')),
  Neo4jUserMemoryDetail: lazy(() => import('@/views/UserMemoryDetail/Neo4j')),
  MemberManagement: lazy(() => import('@/views/MemberManagement')),
  MemoryManagement: lazy(() => import('@/views/MemoryManagement')),
  ForgettingEngine: lazy(() => import('@/views/ForgettingEngine')),
  MemoryExtractionEngine: lazy(() => import('@/views/MemoryExtractionEngine')),
  ApplicationManagement: lazy(() => import('@/views/ApplicationManagement')),
  ApplicationConfig: lazy(() => import('@/views/ApplicationConfig')),
  MemoryConversation: lazy(() => import('@/views/MemoryConversation')),
  Conversation: lazy(() => import('@/views/Conversation')),
  KnowledgeBase: lazy(() => import('@/views/KnowledgeBase')),
  Private: lazy(() => import('@/views/KnowledgeBase/[knowledgeBaseId]/Private')),
  Share: lazy(() => import('@/views/KnowledgeBase/[knowledgeBaseId]/Share')),
  CreateDataset: lazy(() => import('@/views/KnowledgeBase/[knowledgeBaseId]/CreateDataset')),
  DocumentDetails: lazy(() => import('@/views/KnowledgeBase/[knowledgeBaseId]/DocumentDetails')),
  UserManagement: lazy(() => import('@/views/UserManagement')),
  ModelManagement: lazy(() => import('@/views/ModelManagement')),
  SpaceManagement: lazy(() => import('@/views/SpaceManagement')),
  ApiKeyManagement: lazy(() => import('@/views/ApiKeyManagement')),
  EmotionEngine: lazy(() => import('@/views/EmotionEngine')),
  StatementDetail: lazy(() => import('@/views/UserMemoryDetail/pages/StatementDetail')),
  SelfReflectionEngine: lazy(() => import('@/views/SelfReflectionEngine')),
  OrderPayment: lazy(() => import('@/views/OrderPayment')),
  OrderHistory: lazy(() => import('@/views/OrderHistory')),
  Pricing: lazy(() => import('@/views/Pricing')),
  ToolManagement: lazy(() => import('@/views/ToolManagement')),
  Login: lazy(() => import('@/views/Login')),
  InviteRegister: lazy(() => import('@/views/InviteRegister')),
  NoPermission: lazy(() => import('@/views/NoPermission')),
  NotFound: lazy(() => import('@/views/NotFound'))
};

// 检查并报告缺失的组件
const allElements = collectElements(routesConfig);
allElements.forEach(elementName => {
  if (!componentMap[elementName]) {
    console.warn(`Warning: Component ${elementName} is referenced in routes but not defined in componentMap`);
  }
});

// 确保NotFound组件总是存在作为兜底
if (!componentMap['NotFound']) {
  componentMap['NotFound'] = lazy(() => import('@/views/NotFound/index.tsx'));
}

// 路由配置类型定义
interface RouteConfig {
  path?: string;
  element: string;
  componentPath?: string;
  children?: RouteConfig[];
}

// 递归函数，用于生成路由元素
const generateRoutes = (routes: RouteConfig[]): ReactNode => {
  return routes.map((route, index) => {
    // 获取组件
    const componentKey = route.element as keyof typeof componentMap;
    const Component = componentMap[componentKey];

    if (!Component) {
      console.error(`Component ${route.element} not found in componentMap`);
      return null;
    }

    // 如果有子路由
    if (route.children) {
      return (
        <Route key={index} element={<Component />}>
          {generateRoutes(route.children)}
        </Route>
      );
    }

    // 如果有path属性，则为普通路由
    if (route.path) {
      return <Route key={index} path={route.path} element={<Component />} />;
    }

    return null;
  });
};

// 创建路由
const router = createHashRouter(
  createRoutesFromElements(
    generateRoutes(routesConfig)
  )
);

export default router;