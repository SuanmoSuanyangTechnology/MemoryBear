/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:33:11 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-04 18:11:34
 */
/**
 * Route Configuration
 * 
 * Manages application routing with:
 * - Dynamic route generation from JSON configuration
 * - Lazy loading of components for code splitting
 * - Nested route support
 * - Component mapping validation
 * - Hash-based routing
 * 
 * @module routes
 */

import { lazy, type LazyExoticComponent, type ComponentType, type ReactNode } from 'react';
import { createHashRouter, createRoutesFromElements, Route } from 'react-router-dom';

/** Import route configuration JSON */
import routesConfig from './routes.json';

/** Recursively collect all element names from routes */
function collectElements(routes: RouteConfig[]): Set<string> {
  const elements = new Set<string>();

  function traverse(routeList: RouteConfig[]) {
    routeList.forEach(route => {
      /** Add current route's element */
      elements.add(route.element);

      /** Recursively process child routes */
      if (route.children && route.children.length > 0) {
        traverse(route.children);
      }
    });
  }

  traverse(routes);
  return elements;
}

/** Component mapping table - maps element names to lazy-loaded components */
const componentMap: Record<string, LazyExoticComponent<ComponentType<object>>> = {
  /** Layout components */
  AuthLayout: lazy(() => import('@/components/Layout/AuthLayout')),
  AuthSpaceLayout: lazy(() => import('@/components/Layout/AuthSpaceLayout')),
  BasicLayout: lazy(() => import('@/components/Layout/BasicLayout')),
  LoginLayout: lazy(() => import('@/components/Layout/LoginLayout')),
  NoAuthLayout: lazy(() => import('@/components/Layout/NoAuthLayout')),
  /** View components */
  Index: lazy(() => import('@/views/Index')),
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
  ForgetDetail: lazy(() => import('@/views/UserMemoryDetail/pages/ForgetDetail')),
  MemoryNodeDetail: lazy(() => import('@/views/UserMemoryDetail/pages/index')),
  SelfReflectionEngine: lazy(() => import('@/views/SelfReflectionEngine')),
  OrderPayment: lazy(() => import('@/views/OrderPayment')),
  OrderHistory: lazy(() => import('@/views/OrderHistory')),
  Pricing: lazy(() => import('@/views/Pricing')),
  ToolManagement: lazy(() => import('@/views/ToolManagement')),
  SpaceConfig: lazy(() => import('@/views/SpaceConfig')),
  Ontology: lazy(() => import('@/views/Ontology')),
  OntologyDetail: lazy(() => import('@/views/Ontology/pages/Detail')),
  Prompt: lazy(() => import('@/views/Prompt')),
  Jump: lazy(() => import('@/views/JumpPage')),
  Login: lazy(() => import('@/views/Login')),
  InviteRegister: lazy(() => import('@/views/InviteRegister')),
  NoPermission: lazy(() => import('@/views/NoPermission')),
  NotFound: lazy(() => import('@/views/NotFound'))
};

/** Check and report missing components */
const allElements = collectElements(routesConfig);
allElements.forEach(elementName => {
  if (!componentMap[elementName]) {
    console.warn(`Warning: Component ${elementName} is referenced in routes but not defined in componentMap`);
  }
});

/** Ensure NotFound component always exists as fallback */
if (!componentMap['NotFound']) {
  componentMap['NotFound'] = lazy(() => import('@/views/NotFound/index.tsx'));
}

/** Route configuration type definition */
interface RouteConfig {
  /** Route path */
  path?: string;
  /** Component element name */
  element: string;
  /** Component file path (optional) */
  componentPath?: string;
  /** Child routes */
  children?: RouteConfig[];
}

/** Recursively generate route elements from configuration */
const generateRoutes = (routes: RouteConfig[]): ReactNode => {
  return routes.map((route, index) => {
    /** Get component from mapping */
    const componentKey = route.element as keyof typeof componentMap;
    const Component = componentMap[componentKey];

    if (!Component) {
      console.error(`Component ${route.element} not found in componentMap`);
      return null;
    }

    /** If has child routes, create nested route */
    if (route.children) {
      return (
        <Route key={index} element={<Component />}>
          {generateRoutes(route.children)}
        </Route>
      );
    }

    /** If has path property, create regular route */
    if (route.path) {
      return <Route key={index} path={route.path} element={<Component />} />;
    }

    return null;
  });
};

/** Create hash router from route configuration */
const router = createHashRouter(
  createRoutesFromElements(
    generateRoutes(routesConfig)
  )
);

export default router;