"""
Transform 节点实现

数据转换节点，用于处理和转换数据。
"""

import logging
from typing import Any

from app.core.workflow.nodes.base_node import BaseNode, WorkflowState

logger = logging.getLogger(__name__)


class TransformNode(BaseNode):
    """数据转换节点
    
    配置示例:
    {
        "type": "transform",
        "config": {
            "mapping": {
                "output_field": "{{node.previous.output}}",
                "processed": "{{var.input | upper}}"
            }
        }
    }
    """
    
    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        """执行数据转换
        
        Args:
            state: 工作流状态
        
        Returns:
            状态更新字典
        """
        logger.info(f"节点 {self.node_id} 开始执行数据转换")
        
        # 获取映射配置
        mapping = self.config.get("mapping", {})
        
        # 执行数据转换
        transformed_data = {}
        for target_key, source_template in mapping.items():
            # 渲染模板获取值
            value = self._render_template(str(source_template), state)
            transformed_data[target_key] = value
        
        logger.info(f"节点 {self.node_id} 数据转换完成，输出字段: {list(transformed_data.keys())}")
        
        return {
            "node_outputs": {
                self.node_id: {
                    "output": transformed_data,
                    "status": "completed"
                }
            }
        }
