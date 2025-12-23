"""
End 节点实现

工作流的结束节点，输出最终结果。
"""

import logging
import re
import asyncio

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
            output = self._render_template(output_template, state)
        else:
            output = "工作流已完成"
        
        # 统计信息（用于日志）
        node_outputs = state.get("node_outputs", {})
        total_nodes = len(node_outputs)
        
        logger.info(f"节点 {self.node_id} (End) 执行完成，共执行 {total_nodes} 个节点")

        return output

    def _extract_referenced_nodes(self, template: str) -> list[str]:
        """从模板中提取引用的节点 ID

        例如：'结果：{{llm_qa.output}}' -> ['llm_qa']

        Args:
            template: 模板字符串

        Returns:
            引用的节点 ID 列表
        """
        # 匹配 {{node_id.xxx}} 格式
        pattern = r'\{\{([a-zA-Z0-9_]+)\.[a-zA-Z0-9_]+\}\}'
        matches = re.findall(pattern, template)
        return list(set(matches))  # 去重

    def _parse_template_parts(self, template: str, state: WorkflowState) -> list[dict]:
        """解析模板，分离静态文本和动态引用

        例如：'你好 {{llm.output}}, 这是后缀'
        返回：[
            {"type": "static", "content": "你好 "},
            {"type": "dynamic", "node_id": "llm", "field": "output"},
            {"type": "static", "content": ", 这是后缀"}
        ]

        Args:
            template: 模板字符串
            state: 工作流状态

        Returns:
            模板部分列表
        """
        import re

        parts = []
        last_end = 0

        # 匹配 {{xxx}} 或 {{ xxx }} 格式（支持空格）
        pattern = r'\{\{\s*([^}]+?)\s*\}\}'

        for match in re.finditer(pattern, template):
            start, end = match.span()

            # 添加前面的静态文本
            if start > last_end:
                static_text = template[last_end:start]
                if static_text:
                    parts.append({"type": "static", "content": static_text})

            # 解析动态引用
            ref = match.group(1).strip()

            # 检查是否是节点引用（如 llm.output 或 llm_qa.output）
            if '.' in ref:
                node_id, field = ref.split('.', 1)
                parts.append({
                    "type": "dynamic",
                    "node_id": node_id,
                    "field": field,
                    "raw": ref
                })
            else:
                # 其他引用（如 {{var.xxx}}），当作静态处理
                # 直接渲染这部分
                rendered = self._render_template(f"{{{{{ref}}}}}", state)
                parts.append({"type": "static", "content": rendered})

            last_end = end

        # 添加最后的静态文本
        if last_end < len(template):
            static_text = template[last_end:]
            if static_text:
                parts.append({"type": "static", "content": static_text})

        return parts

    async def execute_stream(self, state: WorkflowState):
        """流式执行 end 节点业务逻辑

        智能输出策略：
        1. 检测模板中是否引用了直接上游节点
        2. 如果引用了，只输出该引用**之后**的部分（后缀）
        3. 前缀和引用内容已经在上游节点流式输出时发送了

        示例：'{{start.test}}hahaha {{ llm_qa.output }} lalalalala a'
        - 直接上游节点是 llm_qa
        - 前缀 '{{start.test}}hahaha ' 已在 LLM 节点流式输出前发送
        - LLM 内容在 LLM 节点流式输出
        - End 节点只输出 ' lalalalala a'（后缀，一次性输出）

        Args:
            state: 工作流状态

        Yields:
            完成标记
        """
        logger.info(f"节点 {self.node_id} (End) 开始执行（流式）")

        # 获取配置的输出模板
        output_template = self.config.get("output")

        if not output_template:
            output = "工作流已完成"
            yield {"__final__": True, "result": output}
            return

        # 找到直接上游节点
        direct_upstream_nodes = []
        for edge in self.workflow_config.get("edges", []):
            if edge.get("target") == self.node_id:
                source_node_id = edge.get("source")
                direct_upstream_nodes.append(source_node_id)

        logger.info(f"节点 {self.node_id} 的直接上游节点: {direct_upstream_nodes}")

        # 解析模板部分
        parts = self._parse_template_parts(output_template, state)
        logger.info(f"节点 {self.node_id} 解析模板，共 {len(parts)} 个部分")
        for i, part in enumerate(parts):
            logger.info(f"[模板解析] part[{i}]: {part}")

        # 找到第一个引用直接上游节点的动态引用
        upstream_ref_index = None
        for i, part in enumerate(parts):
            if part["type"] == "dynamic" and part["node_id"] in direct_upstream_nodes:
                upstream_ref_index = i
                logger.info(f"节点 {self.node_id} 找到直接上游节点 {part['node_id']} 的引用，索引: {i}")
                break

        if upstream_ref_index is None:
            # 没有引用直接上游节点，输出完整模板内容
            output = self._render_template(output_template, state)
            logger.info(f"节点 {self.node_id} 没有引用直接上游节点，输出完整内容: '{output[:50]}...'")

            # 通过 writer 发送完整内容（作为一个 message chunk）
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
            writer({
                "type": "message",  # End 节点的输出使用 message 类型
                "node_id": self.node_id,
                "chunk": output,
                "full_content": output,
                "chunk_index": 1,
                "is_suffix": False
            })
            logger.info(f"节点 {self.node_id} 已通过 writer 发送完整内容")

            # yield 完成标记
            yield {"__final__": True, "result": output}
            return

        # 有引用直接上游节点，只输出该引用之后的部分（后缀）
        logger.info(f"节点 {self.node_id} 检测到直接上游节点引用，只输出后缀部分（从索引 {upstream_ref_index + 1} 开始）")

        # 收集后缀部分
        suffix_parts = []
        logger.info(f"[后缀调试] 开始收集后缀，从索引 {upstream_ref_index + 1} 到 {len(parts) - 1}")
        for i in range(upstream_ref_index + 1, len(parts)):
            part = parts[i]
            logger.info(f"[后缀调试] 处理 part[{i}]: {part}")
            if part["type"] == "static":
                # 静态文本
                logger.info(f"[后缀调试] 添加静态文本: '{part['content']}'")
                suffix_parts.append(part["content"])

            elif part["type"] == "dynamic":
                # Other dynamic references (if there are multiple references)
                node_id = part["node_id"]
                field = part["field"]
                
                # Use VariablePool to get variable value
                pool = self.get_variable_pool(state)
                try:
                    # Try to get variable value with default empty string
                    content = pool.get([node_id, field], default="")
                    logger.info(f"[后缀调试] 获取变量 {node_id}.{field} 成功: '{content}'")
                except Exception as e:
                    logger.warning(f"[后缀调试] 获取变量 {node_id}.{field} 失败: {e}")
                    content = ""

                # Convert to string if not None
                suffix_parts.append(str(content) if content is not None else "")
        
        # 拼接后缀
        suffix = "".join(suffix_parts)

        # 构建完整输出（用于返回，包含前缀 + 动态内容 + 后缀）
        full_output = self._render_template(output_template, state)

        logger.info(f"[后缀调试] 节点 {self.node_id} 后缀部分数量: {len(suffix_parts)}")
        logger.info(f"[后缀调试] 后缀内容: '{suffix}'")
        logger.info(f"[后缀调试] 后缀长度: {len(suffix)}")
        logger.info(f"[后缀调试] 后缀是否为空: {not suffix}")

        if suffix:
            logger.info(f"节点 {self.node_id} 输出后缀: '{suffix}...' (长度: {len(suffix)})")
            # 一次性输出后缀（作为单个 chunk）
            # 注意：不要直接 yield 字符串，因为 base_node 会逐字符处理
            # 而是通过 writer 直接发送
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
            writer({
                "type": "message",  # End 节点的输出使用 message 类型
                "node_id": self.node_id,
                "chunk": suffix,
                "full_content": full_output,  # full_content 是完整的渲染结果（前缀+LLM+后缀）
                "chunk_index": 1,
                "is_suffix": True
            })
            logger.info(f"节点 {self.node_id} 已通过 writer 发送后缀，full_content 长度: {len(full_output)}")
        else:
            logger.warning(f"[后缀调试] 节点 {self.node_id} 后缀为空，不发送！upstream_ref_index={upstream_ref_index}, parts数量={len(parts)}")
        
        # 统计信息
        node_outputs = state.get("node_outputs", {})
        total_nodes = len(node_outputs)

        logger.info(f"节点 {self.node_id} (End) 执行完成（流式），共执行了 {total_nodes} 个节点")

        # yield 完成标记（包含完整输出）
        yield {"__final__": True, "result": full_output}
