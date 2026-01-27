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
    支持实时流式输出：如果模板引用了上游节点的输出，会实时监听其流式缓冲区。
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
            output = self._render_template(output_template, state, strict=False)
        else:
            output = ""

        # 统计信息（用于日志）
        node_outputs = state.get("node_outputs", {})
        total_nodes = len(node_outputs)

        logger.info(f"节点 {self.node_id} (End) 执行完成，共执行 {total_nodes} 个节点")

        return output
