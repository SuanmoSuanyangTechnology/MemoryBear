"""JSON转换工具 - 数据格式转换"""
import json
import time
from typing import List, Any, Dict
import yaml
import xml.etree.ElementTree as ET
from xml.dom import minidom

from app.core.tools.base import ToolParameter, ToolResult, ParameterType
from .base import BuiltinTool


class JsonTool(BuiltinTool):
    """JSON转换工具 - 提供JSON格式化、压缩、验证、格式转换功能"""
    
    @property
    def name(self) -> str:
        return "json_tool"
    
    @property
    def description(self) -> str:
        return "JSON转换工具 - 数据格式转换：JSON格式化、JSON压缩、JSON验证、格式转换"
    
    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                type=ParameterType.STRING,
                description="操作类型",
                required=True,
                enum=["format", "minify", "validate", "convert", "to_yaml", "from_yaml", "to_xml", "from_xml", "merge", "extract"]
            ),
            ToolParameter(
                name="input_data",
                type=ParameterType.STRING,
                description="输入数据（JSON字符串、YAML字符串或XML字符串）",
                required=True
            ),
            ToolParameter(
                name="indent",
                type=ParameterType.INTEGER,
                description="JSON格式化缩进空格数",
                required=False,
                default=2,
                minimum=0,
                maximum=8
            ),
            ToolParameter(
                name="ensure_ascii",
                type=ParameterType.BOOLEAN,
                description="是否确保ASCII编码",
                required=False,
                default=False
            ),
            ToolParameter(
                name="sort_keys",
                type=ParameterType.BOOLEAN,
                description="是否对键进行排序",
                required=False,
                default=False
            ),
            ToolParameter(
                name="merge_data",
                type=ParameterType.STRING,
                description="要合并的JSON数据（用于merge操作）",
                required=False
            ),
            ToolParameter(
                name="json_path",
                type=ParameterType.STRING,
                description="JSON路径表达式（用于extract操作，如：$.user.name）",
                required=False
            )
        ]
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行JSON工具操作"""
        start_time = time.time()
        
        try:
            operation = kwargs.get("operation")
            input_data = kwargs.get("input_data")
            
            if not input_data:
                raise ValueError("input_data 参数是必需的")
            
            if operation == "format":
                result = self._format_json(input_data, kwargs)
            elif operation == "minify":
                result = self._minify_json(input_data)
            elif operation == "validate":
                result = self._validate_json(input_data)
            elif operation == "convert":
                result = self._convert_json(input_data)
            elif operation == "to_yaml":
                result = self._json_to_yaml(input_data)
            elif operation == "from_yaml":
                result = self._yaml_to_json(input_data, kwargs)
            elif operation == "to_xml":
                result = self._json_to_xml(input_data)
            elif operation == "from_xml":
                result = self._xml_to_json(input_data, kwargs)
            elif operation == "merge":
                result = self._merge_json(input_data, kwargs)
            elif operation == "extract":
                result = self._extract_json_path(input_data, kwargs)
            else:
                raise ValueError(f"不支持的操作类型: {operation}")
            
            execution_time = time.time() - start_time
            return ToolResult.success_result(
                data=result,
                execution_time=execution_time
            )
            
        except Exception as e:
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="JSON_ERROR",
                execution_time=execution_time
            )

    @staticmethod
    def _format_json(input_data: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """格式化JSON"""
        indent = kwargs.get("indent", 2)
        ensure_ascii = kwargs.get("ensure_ascii", False)
        sort_keys = kwargs.get("sort_keys", False)
        
        # 解析JSON
        data = json.loads(input_data)
        
        # 格式化输出
        formatted = json.dumps(
            data,
            indent=indent,
            ensure_ascii=ensure_ascii,
            sort_keys=sort_keys,
            separators=(',', ': ')
        )
        
        return {
            "original_size": len(input_data),
            "formatted_size": len(formatted),
            "formatted_json": formatted,
            "is_valid": True,
            "settings": {
                "indent": indent,
                "ensure_ascii": ensure_ascii,
                "sort_keys": sort_keys
            }
        }

    @staticmethod
    def _minify_json(input_data: str) -> Dict[str, Any]:
        """压缩JSON"""
        # 解析并压缩
        data = json.loads(input_data)
        minified = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        
        return {
            "original_size": len(input_data),
            "minified_size": len(minified),
            "compression_ratio": round((1 - len(minified) / len(input_data)) * 100, 2),
            "minified_json": minified,
            "is_valid": True
        }

    def _validate_json(self, input_data: str) -> Dict[str, Any]:
        """验证JSON"""
        try:
            data = json.loads(input_data)
            
            # 统计信息
            stats = self._analyze_json_structure(data)
            
            return {
                "is_valid": True,
                "error": None,
                "size": len(input_data),
                "structure": stats
            }
            
        except json.JSONDecodeError as e:
            return {
                "is_valid": False,
                "error": str(e),
                "error_line": getattr(e, 'lineno', None),
                "error_column": getattr(e, 'colno', None),
                "size": len(input_data)
            }

    @staticmethod
    def _convert_json(input_data: str) -> Dict[str, Any]:
        """JSON转义"""
        data = json.loads(input_data)
        converted = json.dumps(data, ensure_ascii=True, separators=(',', ':'))

        return {
            "converted_json": converted,
            "is_valid": True
        }

    @staticmethod
    def _json_to_yaml(input_data: str) -> Dict[str, Any]:
        """JSON转YAML"""
        data = json.loads(input_data)
        yaml_output = yaml.dump(data, default_flow_style=False, allow_unicode=True, indent=2)
        
        return {
            "original_format": "json",
            "target_format": "yaml",
            "original_size": len(input_data),
            "converted_size": len(yaml_output),
            "converted_data": yaml_output
        }

    @staticmethod
    def _yaml_to_json(input_data: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """YAML转JSON"""
        indent = kwargs.get("indent", 2)
        ensure_ascii = kwargs.get("ensure_ascii", False)
        
        data = yaml.safe_load(input_data)
        json_output = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
        
        return {
            "original_format": "yaml",
            "target_format": "json",
            "original_size": len(input_data),
            "converted_size": len(json_output),
            "converted_data": json_output
        }

    @staticmethod
    def _json_to_xml(input_data: str) -> Dict[str, Any]:
        """JSON转XML"""
        json_data = json.loads(input_data)
        
        def dict_to_xml(data, root_name="root"):
            """递归转换字典为XML"""
            if isinstance(data, dict):
                if len(data) == 1 and not root_name == "root":
                    # 如果字典只有一个键，使用该键作为根元素
                    key, value = next(iter(data.items()))
                    return dict_to_xml(value, key)
                
                root = ET.Element(root_name)
                for key, value in data.items():
                    if isinstance(value, (dict, list)):
                        child = dict_to_xml(value, key)
                        root.append(child)
                    else:
                        child = ET.SubElement(root, key)
                        child.text = str(value)
                return root
            
            elif isinstance(data, list):
                root = ET.Element(root_name)
                for i, item in enumerate(data):
                    if isinstance(item, (dict, list)):
                        child = dict_to_xml(item, f"item_{i}")
                        root.append(child)
                    else:
                        child = ET.SubElement(root, f"item_{i}")
                        child.text = str(item)
                return root
            
            else:
                root = ET.Element(root_name)
                root.text = str(data)
                return root
        
        xml_element = dict_to_xml(json_data)
        xml_string = ET.tostring(xml_element, encoding='unicode')
        
        # 格式化XML
        dom = minidom.parseString(xml_string)
        formatted_xml = dom.toprettyxml(indent="  ")
        
        # 移除空行
        formatted_xml = '\n'.join([line for line in formatted_xml.split('\n') if line.strip()])
        
        return {
            "original_format": "json",
            "target_format": "xml",
            "original_size": len(input_data),
            "converted_size": len(formatted_xml),
            "converted_data": formatted_xml
        }

    @staticmethod
    def _xml_to_json(input_data: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """XML转JSON"""
        indent = kwargs.get("indent", 2)
        
        def xml_to_dict(element):
            """递归转换XML元素为字典"""
            result = {}
            
            # 处理属性
            if element.attrib:
                result.update(element.attrib)
            
            # 处理文本内容
            if element.text and element.text.strip():
                if len(element) == 0:  # 叶子节点
                    return element.text.strip()
                else:
                    result['text'] = element.text.strip()
            
            # 处理子元素
            for child in element:
                child_data = xml_to_dict(child)
                if child.tag in result:
                    # 如果标签已存在，转换为列表
                    if not isinstance(result[child.tag], list):
                        result[child.tag] = [result[child.tag]]
                    result[child.tag].append(child_data)
                else:
                    result[child.tag] = child_data
            
            return result
        
        root = ET.fromstring(input_data)
        data = {root.tag: xml_to_dict(root)}
        json_output = json.dumps(data, indent=indent, ensure_ascii=False)
        
        return {
            "original_format": "xml",
            "target_format": "json",
            "original_size": len(input_data),
            "converted_size": len(json_output),
            "converted_data": json_output
        }

    @staticmethod
    def _merge_json(input_data: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """合并JSON"""
        merge_data = kwargs.get("merge_data")
        if not merge_data:
            raise ValueError("merge_data 参数是必需的")
        
        data1 = json.loads(input_data)
        data2 = json.loads(merge_data)
        
        def deep_merge(dict1, dict2):
            """深度合并字典"""
            result = dict1.copy()
            for key, value in dict2.items():
                if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = deep_merge(result[key], value)
                else:
                    result[key] = value
            return result
        
        if isinstance(data1, dict) and isinstance(data2, dict):
            merged = deep_merge(data1, data2)
        elif isinstance(data1, list) and isinstance(data2, list):
            merged = data1 + data2
        else:
            raise ValueError("无法合并不同类型的数据")
        
        merged_json = json.dumps(merged, indent=2, ensure_ascii=False)
        
        return {
            "operation": "merge",
            "original_size": len(input_data),
            "merge_size": len(merge_data),
            "result_size": len(merged_json),
            "merged_data": merged_json
        }

    @staticmethod
    def _extract_json_path( input_data: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """提取JSON路径"""
        json_path = kwargs.get("json_path")
        if not json_path:
            raise ValueError("json_path 参数是必需的")
        
        data = json.loads(input_data)
        
        # 简单的JSONPath实现（支持基本的点号路径）
        try:
            result = data
            if json_path.startswith('$.'):
                path_parts = json_path[2:].split('.')
            else:
                path_parts = json_path.split('.')
            
            for part in path_parts:
                if part.isdigit():
                    result = result[int(part)]
                else:
                    result = result[part]
            
            extracted_json = json.dumps(result, indent=2, ensure_ascii=False)
            
            return {
                "operation": "extract",
                "json_path": json_path,
                "found": True,
                "extracted_data": extracted_json,
                "data_type": type(result).__name__
            }
            
        except (KeyError, IndexError, TypeError) as e:
            return {
                "operation": "extract",
                "json_path": json_path,
                "found": False,
                "error": str(e),
                "extracted_data": None
            }
    
    def _analyze_json_structure(self, data: Any, depth: int = 0) -> Dict[str, Any]:
        """分析JSON结构"""
        if isinstance(data, dict):
            return {
                "type": "object",
                "keys": len(data),
                "depth": depth,
                "children": {k: self._analyze_json_structure(v, depth + 1) for k, v in data.items()}
            }
        elif isinstance(data, list):
            return {
                "type": "array",
                "length": len(data),
                "depth": depth,
                "item_types": list(set(type(item).__name__ for item in data))
            }
        else:
            return {
                "type": type(data).__name__,
                "depth": depth,
                "value": str(data)[:100] + "..." if len(str(data)) > 100 else str(data)
            }