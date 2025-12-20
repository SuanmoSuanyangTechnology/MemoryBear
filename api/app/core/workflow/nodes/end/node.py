"""
End 节点实现

工作流的结束节点，输出最终结果。
"""

import logging

from app.core.workflow.nodes.base_node import BaseNode, WorkflowState

logger = logging.getLogger(__name__)


class EndNode(BaseNode):
    """End 节点
    
    工作流的结束节点，根据配置的模板输出最终结果。
    """
    
    async def execute(self, state: WorkflowState) -> str:
        """执行 end 节点业务逻辑
        
        Args:
            state: 工作流状态
        
        Returns:
            最终输出字符串
        """
        logger.info(f"节点 {self.node_id} (End) 开始执行")
        
        # 获取配置的输出模板
        output_template = self.config.get("output")
        
        # 如果配置了输出模板，使用模板渲染；否则使用默认输出
        if output_template:
            output = self._render_template(output_template, state)
        else:
            output = "工作流已完成"
        
        # 统计信息（用于日志）
        node_outputs = state.get("node_outputs", {})
        total_nodes = len(node_outputs)
        
        logger.info(f"节点 {self.node_id} (End) 执行完成，共执行 {total_nodes} 个节点")
        
        return output
    
    async def execute_stream(self, state: WorkflowState):
        """流式执行 end 节点业务逻辑
        
        当 end 节点前面是 LLM 节点时，流式输出其内容。
        
        Args:
            state: 工作流状态
        
        Yields:
            文本片段（chunk）或完成标记
        """
        logger.info(f"节点 {self.node_id} (End) 开始执行（流式）")
        
        # 获取配置的输出模板
        output_template = self.config.get("output")
        
        # 如果配置了输出模板，使用模板渲染
        if output_template:
            output = self._render_template(output_template, state)
            
            # 检查输出中是否包含节点引用（如 {{llm_node.output}}）
            # 如果包含，则逐字符流式输出
            if output:
                # 逐字符流式输出
                for char in output:
                    yield char
        else:
            output = "工作流已完成"
            for char in output:
                yield char
        
        # 统计信息（用于日志）
        node_outputs = state.get("node_outputs", {})
        total_nodes = len(node_outputs)
        
        logger.info(f"节点 {self.node_id} (End) 执行完成（流式），共执行 {total_nodes} 个节点")
        
        # yield 完成标记
        yield {"__final__": True, "result": output}
