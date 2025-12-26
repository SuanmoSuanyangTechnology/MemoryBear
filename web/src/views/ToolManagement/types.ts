type ToolType = 'mcp' | 'builtin' | 'custom'
export interface Query {
  name?: string;
  tool_type: ToolType
}

interface ApiKeyAuth {
  key_name: string;
  api_key: string;
}
interface BasicAuth {
  username: string;
  password: string;
}
interface BearerTokenAuth {
  token: string;
}
export interface MCPToolItem {
  name: string;
  description: string;
  icon: string | null;
  tool_type: ToolType;
  config: {
    server_url: string;
    connection_config: {
      auth_type: 'none' | 'api_key' | 'basic_auth' | 'bearer_token';
      auth_config: ApiKeyAuth | BasicAuth | BearerTokenAuth;
      timeout: number;
      headers: Record<string, string>;
    };
  }
}
export interface InnerToolItem {
  config: {
    tool_class: string;
    requires_config: boolean;
    is_enabled: boolean;
    parameters: {
      api_key: string;
    }
  }
}
export interface CustomToolItem {
  name: string;
  description: string; 
  icon?: string | null;
  tool_type: string;
  config: {
    auth_type: 'none' | 'api_key' | 'basic_auth' | 'bearer_token';
    auth_config: ApiKeyAuth | BasicAuth | BearerTokenAuth;
    schema_content: string;
    schema_url: null;
  }
}
export interface ToolItem {
  id: string;
  name: string;
  description: string;
  icon: string | null;
  tool_type: ToolType;
  version: string;
  parameters: any[];
  config_data: {
    server_url: string;
    connection_config: {
      auth_type: 'none' | 'api_key' | 'basic_auth' | 'bearer_token';
      auth_config: ApiKeyAuth | BasicAuth | BearerTokenAuth;
      timeout: number;
      headers: Record<string, string>;
    };
    last_health_check: number | null;
    health_status: 'unknown' | 'healthy' | 'unhealthy';
    available_tools: any[];

    tool_class: string;

    schema_content: string;
  };
  status: 'available' | 'unavailable';
  tags: string[];
  tenant_id: string;
  created_at: number;
}


export interface McpServiceModalRef {
  handleOpen: (data?: ToolItem) => void;
  handleClose: () => void;
}
export interface TimeToolModalRef {
  handleOpen: (data: ToolItem) => void;
}
export interface JsonToolModalRef {
  handleOpen: (data: ToolItem) => void;
}
export interface InnerToolModalRef {
  handleOpen: (data: ToolItem) => void;
}

export interface ConfigItem {
  name: string | string[];
  type: 'input' | 'select' | 'checkbox' | 'number';
  desc?: string;
  rules?: any[];
  options?: { label: string; value: string }[];
  range?: { [key: string]: number[]}
  min?: number;
  max?: number;
  step?: number;
  defaultValue?: any;
}
export interface InnerConfigItem {
  link?: string;
  config?: Record<string, ConfigItem>
  features: string[];
  eg?: string;
}

export interface ExecuteData {
  tool_id: string;
  parameters: {
    operation: string;
    // 时间戳转换日期时间
    input_value?: string;
    output_format?: string;
    to_timezone?: string;
    input_format?: string;
    from_timezone?: string;
    indent?: number;
    ensure_ascii?: boolean;
    sort_keys?: boolean;
    input_data?: string;
  }
}
export interface CustomToolModalRef {
  handleOpen: (data?: ToolItem) => void;
  handleClose: () => void;
}