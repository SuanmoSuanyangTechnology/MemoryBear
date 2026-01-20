"""
OpenAI LLM 客户端实现

基于 LangChain 和 RedBearLLM 的 OpenAI 客户端实现。
"""

import asyncio
import json
import logging
from typing import Any, Dict, List

from app.core.config import settings
from app.core.memory.llm_tools.llm_client import LLMClient, LLMClientException
from app.core.models.base import RedBearModelConfig
from app.core.models.llm import RedBearLLM
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class OpenAIClient(LLMClient):
    """
    OpenAI LLM 客户端实现

    基于 LangChain 和 RedBearLLM 的实现，支持：
    - 聊天接口
    - 结构化输出
    - Langfuse 追踪（可选）
    """

    def __init__(self, model_config: RedBearModelConfig, type_: str = "chat"):
        """
        初始化 OpenAI 客户端

        Args:
            model_config: 模型配置
            type_: 模型类型，"chat" 或 "completion"
        """
        super().__init__(model_config)

        # 初始化 Langfuse 回调处理器（如果启用）
        self.langfuse_handler = None
        if settings.LANGFUSE_ENABLED:
            try:
                from langfuse.langchain import CallbackHandler
                self.langfuse_handler = CallbackHandler()
                logger.info("Langfuse 追踪已启用")
            except ImportError:
                logger.warning("Langfuse 未安装，跳过追踪功能")
            except Exception as e:
                logger.warning(f"初始化 Langfuse 处理器失败: {e}")

        # 初始化 RedBearLLM 客户端
        self.client = RedBearLLM(
            RedBearModelConfig(
                model_name=self.model_name,
                provider=self.provider,
                api_key=self.api_key,
                base_url=self.base_url,
                max_retries=self.max_retries,
                timeout=self.timeout,
            ),
            type=type_
        )

        logger.info(f"OpenAI 客户端初始化完成: type={type_}")

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> Any:
        """
        聊天接口实现

        Args:
            messages: 消息列表
            **kwargs: 额外参数

        Returns:
            LLM 响应内容

        Raises:
            LLMClientException: LLM 调用失败
        """
        try:
            template = """{messages}"""
            prompt = ChatPromptTemplate.from_template(template)
            chain = prompt | self.client

            # 添加 Langfuse 回调（如果可用）
            config = {}
            if self.langfuse_handler:
                config["callbacks"] = [self.langfuse_handler]

            response = await chain.ainvoke({"messages": messages}, config=config)

            logger.debug(f"LLM 响应成功: {len(str(response))} 字符")
            return response

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise LLMClientException(f"LLM 调用失败: {e}") from e

    async def response(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """
        简单响应接口实现（用于fallback机制）

        Args:
            messages: 消息列表
            **kwargs: 额外参数

        Returns:
            LLM 响应文本

        Raises:
            LLMClientException: LLM 调用失败
        """
        try:
            template = """{messages}"""
            prompt = ChatPromptTemplate.from_template(template)
            chain = prompt | self.client

            # 添加 Langfuse 回调（如果可用）
            config = {}
            if self.langfuse_handler:
                config["callbacks"] = [self.langfuse_handler]

            response = await chain.ainvoke({"messages": messages}, config=config)

            # 提取文本内容
            if hasattr(response, "content"):
                return str(response.content)
            return str(response)

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise LLMClientException(f"LLM 调用失败: {e}") from e

    async def response_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: type[BaseModel],
        **kwargs
    ) -> BaseModel:
        """
        结构化输出接口实现

        Args:
            messages: 消息列表
            response_model: 期望的响应模型类型
            **kwargs: 额外参数

        Returns:
            解析后的 Pydantic 模型实例

        Raises:
            LLMClientException: LLM 调用或解析失败
        """
        try:
            # 构建问题文本
            question_text = "\n\n".join([
                str(m.get("content", "")) for m in messages
            ])

            # 准备配置（包含 Langfuse 回调）
            config = {}
            if self.langfuse_handler:
                config["callbacks"] = [self.langfuse_handler]

            template = """{question}"""
            prompt = ChatPromptTemplate.from_template(template)

            # 对于 DashScope 等不支持 with_structured_output 的模型，优先使用手动JSON解析
            # 这样可以避免不必要的尝试和错误
            if self.provider: #.lower() == "dashscope"
                logger.info("DashScope 模型，直接使用手动JSON解析方法")
                try:
                    # 获取原始响应，添加超时保护
                    chain = prompt | self.client
                    response = await asyncio.wait_for(
                        chain.ainvoke({"question": question_text}, config=config),
                        timeout=self.timeout
                    )

                    # 提取响应文本
                    response_text = ""
                    if hasattr(response, "content"):
                        response_text = str(response.content)
                    else:
                        response_text = str(response)

                    logger.debug(f"LLM原始响应长度: {len(response_text)}")

                    # 尝试提取JSON内容
                    json_text = response_text.strip()

                    # 如果响应包含markdown代码块，提取其中的JSON
                    if "```json" in json_text:
                        json_text = json_text.split("```json")[1].split("```")[0].strip()
                    elif "```" in json_text:
                        json_text = json_text.split("```")[1].split("```")[0].strip()

                    # 尝试修复常见的JSON格式问题
                    # 1. 移除可能的BOM标记
                    json_text = json_text.lstrip('\ufeff')

                    # 2. 如果JSON被截断（缺少结尾的 ] 或 }），尝试修复
                    if json_text.startswith('[') and not json_text.rstrip().endswith(']'):
                        logger.warning("检测到JSON数组被截断，尝试修复")
                        # 找到最后一个完整的对象
                        last_complete_brace = json_text.rfind('}')
                        if last_complete_brace > 0:
                            json_text = json_text[:last_complete_brace + 1] + ']'
                            logger.info(f"修复后的JSON长度: {len(json_text)}")
                    elif json_text.startswith('{') and not json_text.rstrip().endswith('}'):
                        logger.warning("检测到JSON对象被截断，尝试修复")
                        # 找到最后一个完整的字段
                        last_complete_brace = json_text.rfind('}')
                        if last_complete_brace > 0:
                            json_text = json_text[:last_complete_brace + 1]
                            logger.info(f"修复后的JSON长度: {len(json_text)}")

                    # 解析JSON
                    try:
                        parsed_dict = json.loads(json_text)
                        logger.debug(f"JSON解析成功，类型: {type(parsed_dict)}")

                        # 如果是列表，记录第一个元素的结构
                        if isinstance(parsed_dict, list) and len(parsed_dict) > 0:
                            logger.debug(f"第一个元素的键: {list(parsed_dict[0].keys()) if isinstance(parsed_dict[0], dict) else 'not a dict'}")

                        # 尝试字段映射转换（处理LLM返回格式不匹配的情况）
                        if isinstance(parsed_dict, list):
                            transformed_list = []
                            for item in parsed_dict:
                                if isinstance(item, dict):
                                    transformed_item = {}

                                    # 常见的字段映射规则
                                    field_mappings = {
                                        'question': ['extended_question', 'question', 'query'],
                                        'original_question': ['original_question', 'original', 'source_question'],
                                        'extended_question': ['extended_question', 'question', 'query', 'extended'],
                                        'type': ['type', 'category', 'question_type'],
                                        'reason': ['reason', 'explanation', 'rationale'],
                                        'query': ['query', 'question', 'text'],
                                        'split_result': ['split_result', 'result', 'status'],
                                        'expansion_issue': ['expansion_issue', 'issues', 'expansions'],
                                    }

                                    # 对于每个期望的字段，尝试从多个可能的源字段中获取
                                    for target_field, source_fields in field_mappings.items():
                                        for source_field in source_fields:
                                            if source_field in item:
                                                transformed_item[target_field] = item[source_field]
                                                break

                                    # 特殊处理：如果只有 'question' 但缺少 'original_question' 和 'extended_question'
                                    if 'question' in item and 'original_question' not in transformed_item:
                                        transformed_item['original_question'] = item['question']
                                    if 'question' in item and 'extended_question' not in transformed_item:
                                        transformed_item['extended_question'] = item['question']

                                    # 保留原始字段（如果没有被映射）
                                    for key, value in item.items():
                                        if key not in transformed_item:
                                            transformed_item[key] = value

                                    transformed_list.append(transformed_item)
                                else:
                                    transformed_list.append(item)

                            logger.info(f"字段映射完成，尝试重新验证")
                            logger.debug(f"转换后的数据: {transformed_list}")

                            try:
                                return response_model.model_validate(transformed_list)
                            except Exception as retry_error:
                                logger.error(f"字段映射后仍然验证失败: {retry_error}")
                                logger.error(f"完整的LLM响应: {response_text}")
                                logger.error(f"原始解析字典: {parsed_dict}")
                                logger.error(f"转换后的字典: {transformed_list}")
                                raise
                            else:
                                # 非列表类型，记录并抛出原始错误
                                logger.error(f"完整的LLM响应: {response_text}")
                                logger.error(f"解析后的字典: {parsed_dict}")
                                raise
                    except json.JSONDecodeError as je:
                        logger.error(f"JSON解析失败: {je}")
                        logger.error(f"问题位置附近的文本: {json_text[max(0, je.pos-50):min(len(json_text), je.pos+50)]}")

                        # 尝试更激进的修复：逐行解析，找到有效的JSON部分
                        logger.info("尝试逐行解析JSON")
                        lines = json_text.split('\n')
                        for i in range(len(lines), 0, -1):
                            try:
                                partial_json = '\n'.join(lines[:i])
                                if partial_json.startswith('['):
                                    partial_json = partial_json.rstrip().rstrip(',') + ']'
                                elif partial_json.startswith('{'):
                                    partial_json = partial_json.rstrip().rstrip(',') + '}'

                                parsed_dict = json.loads(partial_json)
                                logger.info(f"成功解析部分JSON（前{i}行）")
                                return response_model.model_validate(parsed_dict)
                            except:
                                continue

                        # 如果所有尝试都失败，抛出原始错误
                        raise LLMClientException(f"JSON解析失败: {je}") from je

                except asyncio.TimeoutError:
                    logger.error(f"LLM调用超时（{self.timeout}秒）")
                    raise LLMClientException(f"LLM调用超时（{self.timeout}秒）")
                except LLMClientException:
                    raise
                except Exception as e:
                    logger.error(f"手动JSON解析失败: {e}", exc_info=True)
                    raise LLMClientException(f"手动JSON解析失败: {e}") from e





            # 方法 1: 使用 PydanticOutputParser（适用于支持的模型）
            if PydanticOutputParser is not None:
                try:
                    parser = PydanticOutputParser(pydantic_object=response_model)
                    format_instructions = parser.get_format_instructions()
                    prompt_with_instructions = ChatPromptTemplate.from_template(
                        "{question}\n{format_instructions}"
                    )
                    chain = prompt_with_instructions | self.client | parser

                    parsed = await asyncio.wait_for(
                        chain.ainvoke(
                            {
                                "question": question_text,
                                "format_instructions": format_instructions,
                            },
                            config=config
                        ),
                        timeout=self.timeout
                    )

                    logger.debug(f"使用 PydanticOutputParser 解析成功")
                    return parsed

                except asyncio.TimeoutError:
                    logger.error(f"PydanticOutputParser 调用超时（{self.timeout}秒）")
                    raise LLMClientException(f"LLM调用超时（{self.timeout}秒）")
                except Exception as e:
                    logger.warning(
                        f"PydanticOutputParser 解析失败，尝试其他方法: {e}"
                    )

            # 方法 2: 使用 LangChain 的 with_structured_output (如果支持)
            with_so = getattr(self.client, "with_structured_output", None)
            
            if callable(with_so):
                try:
                    structured_chain = prompt | with_so(response_model, strict=True)
                    parsed = await asyncio.wait_for(
                        structured_chain.ainvoke(
                            {"question": question_text},
                            config=config
                        ),
                        timeout=self.timeout
                    )

                    # 验证并返回结果
                    try:
                        return response_model.model_validate(parsed)
                    except Exception:
                        # 如果已经是 Pydantic 实例，直接返回
                        if hasattr(parsed, "model_dump"):
                            return parsed
                        # 尝试从 JSON 解析
                        return response_model.model_validate_json(json.dumps(parsed))

                except asyncio.TimeoutError:
                    logger.error(f"with_structured_output 调用超时（{self.timeout}秒）")
                    raise LLMClientException(f"LLM调用超时（{self.timeout}秒）")
                except NotImplementedError:
                    logger.warning(
                        f"模型 {self.model_name} 不支持 with_structured_output，使用手动JSON解析"
                    )
                except Exception as e:
                    logger.warning(f"with_structured_output 失败: {e}，尝试手动解析")

            # 方法 3: 手动JSON解析（fallback方法）
            logger.info("使用手动JSON解析方法（fallback）")
            try:
                # 获取原始响应
                chain = prompt | self.client
                response = await asyncio.wait_for(
                    chain.ainvoke({"question": question_text}, config=config),
                    timeout=self.timeout
                )
                
                # 提取响应文本
                response_text = ""
                if hasattr(response, "content"):
                    response_text = str(response.content)
                else:
                    response_text = str(response)
                
                logger.debug(f"LLM原始响应: {response_text[:500]}...")
                
                # 尝试提取JSON内容
                json_text = response_text.strip()
                
                # 如果响应包含markdown代码块，提取其中的JSON
                if "```json" in json_text:
                    json_text = json_text.split("```json")[1].split("```")[0].strip()
                elif "```" in json_text:
                    json_text = json_text.split("```")[1].split("```")[0].strip()
                
                # 解析JSON
                parsed_dict = json.loads(json_text)
                logger.debug(f"JSON解析成功: {parsed_dict}")
                
                # 验证并创建Pydantic模型
                return response_model.model_validate(parsed_dict)
                
            except asyncio.TimeoutError:
                logger.error(f"手动JSON解析调用超时（{self.timeout}秒）")
                raise LLMClientException(f"LLM调用超时（{self.timeout}秒）")
            except json.JSONDecodeError as je:
                logger.error(f"JSON解析失败: {je}, 原始文本: {json_text[:200]}...")
                raise LLMClientException(f"JSON解析失败: {je}") from je
            except Exception as e:
                logger.error(f"手动JSON解析失败: {e}")
                raise LLMClientException(f"手动JSON解析失败: {e}") from e

        except LLMClientException:
            raise
        except Exception as e:
            logger.error(f"结构化输出处理失败: {e}")
            raise LLMClientException(f"结构化输出处理失败: {e}") from e
