import asyncio
from typing import List, Dict, Any
import json

from pydantic import BaseModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

from app.core.models.base import RedBearModelConfig
from app.core.models.llm import RedBearLLM
from app.core.memory.src.llm_tools.llm_client import LLMClient
# from app.core.memory.utils.config.definitions import LANGFUSE_ENABLED
LANGFUSE_ENABLED=False

class OpenAIClient(LLMClient):
    def __init__(self, model_config: RedBearModelConfig, type_: str = "chat"):
        super().__init__(model_config)

        # Initialize Langfuse callback handler if enabled
        self.langfuse_handler = None
        if LANGFUSE_ENABLED:
            try:
                from langfuse.langchain import CallbackHandler
                self.langfuse_handler = CallbackHandler()
            except ImportError:
                # Langfuse not installed, continue without tracing
                pass
            except Exception as e:
                # Log error but don't fail initialization
                import logging
                logging.warning(f"Failed to initialize Langfuse handler: {e}")

        # Initialize RedBearLLM client
        self.client = RedBearLLM(RedBearModelConfig(
            model_name=self.model_name,
            provider=self.provider,
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=self.max_retries,
        ), type=type_)

    async def chat(self, messages: List[Dict[str, str]]) -> Any:
        template = """{messages}"""
        # ChatPromptTemplate
        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | self.client

        # Add Langfuse callback if available
        config = {}
        if self.langfuse_handler:
            config["callbacks"] = [self.langfuse_handler]

        response = await chain.ainvoke({"messages": messages}, config=config)
        # print(f"OpenAIClient response ======>:\n {response}")
        return response

    async def response_structured(
            self,
            messages: List[Dict[str, str]],
            response_model: type[BaseModel],
    ) -> type[BaseModel]:
        # Build a simple prompt pipeline that sends messages to the underlying LLM
        question_text = "\n\n".join([str(m.get("content", "")) for m in messages])

        # Prepare config with Langfuse callback if available
        config = {}
        if self.langfuse_handler:
            config["callbacks"] = [self.langfuse_handler]

        # Primary: enforce schema with PydanticOutputParser if available
        if PydanticOutputParser is not None:
            try:
                import logging
                logger = logging.getLogger(__name__)
                # 使用正确的属性路径：self.config.timeout（从LLMClient基类继承）
                # logger.info(f"开始LLM结构化输出请求 (模型: {self.model_name}, 超时: {self.config.timeout}秒)")

                parser = PydanticOutputParser(pydantic_object=response_model)
                format_instructions = parser.get_format_instructions()
                prompt = ChatPromptTemplate.from_template("{question}\n{format_instructions}")
                chain = prompt | self.client | parser
                parsed = await chain.ainvoke({
                    "question": question_text,
                    "format_instructions": format_instructions,
                })

                # logger.info(f"LLM结构化输出请求成功完成")
                return parsed
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"PydanticOutputParser失败，尝试备用方法: {str(e)}")
                # Fall through to alternative structured methods
                pass

        # Fallback path: create plain prompt for other structured methods
        template = """{question}"""
        prompt = ChatPromptTemplate.from_template(template)

        # Try LangChain structured output if available on the underlying client
        try:
            with_so = getattr(self.client, "with_structured_output", None)

            if callable(with_so):
                try:
                    structured_chain = prompt | with_so(response_model, strict=True)
                    parsed = await structured_chain.ainvoke({"question": question_text}, config=config)
                    # parsed may already be a pydantic model or a dict
                    try:
                        return response_model.model_validate(parsed)
                    except Exception:
                        try:
                            # If it's already a pydantic instance (LangChain returns model), return it
                            if hasattr(parsed, "model_dump"):
                                return parsed
                            return response_model.model_validate_json(json.dumps(parsed))
                        except Exception:
                            # Fall through to manual parsing below
                            pass
                except NotImplementedError:
                    # The underlying model doesn't support structured output, fall through
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(
                        f"Model {self.model_name} doesn't support with_structured_output, falling back to manual parsing")
                    pass
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Structured output attempt failed: {e}, falling back to manual parsing")

        # Final fallback: manual parsing with plain LLM response
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Using manual parsing fallback for model {self.model_name}")
            
            # Create a prompt that asks for JSON output
            json_prompt = ChatPromptTemplate.from_template(
                "{question}\n\n"
                "Please respond with a valid JSON object that matches this schema:\n"
                "{schema}\n\n"
                "Response (JSON only):"
            )
            
            # Get the schema from the response model
            schema = response_model.model_json_schema()
            
            chain = json_prompt | self.client
            response = await chain.ainvoke({
                "question": question_text,
                "schema": json.dumps(schema, indent=2)
            }, config=config)
            
            # Extract JSON from response
            response_text = str(response.content if hasattr(response, 'content') else response)
            
            # Try to find JSON in the response
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                try:
                    parsed_dict = json.loads(json_str)
                    return response_model.model_validate(parsed_dict)
                except json.JSONDecodeError:
                    pass
            
            # If JSON parsing fails, try to create a minimal valid response
            logger.warning(f"Failed to parse JSON from LLM response, creating minimal response")
            
            # Create a minimal response based on the schema
            return self._create_minimal_response(response_model)
            
        except Exception as fallback_error:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Manual parsing fallback also failed: {fallback_error}")
            # Return minimal response as last resort
            return self._create_minimal_response(response_model)

    def _create_minimal_response(self, response_model: type[BaseModel]) -> BaseModel:
        """Create a minimal valid response based on the model schema."""
        try:
            minimal_response = {}
            
            for field_name, field_info in response_model.model_fields.items():
                # Check if field has a default value
                if hasattr(field_info, 'default') and field_info.default is not None:
                    minimal_response[field_name] = field_info.default
                else:
                    # Create default based on field type
                    field_type = field_info.annotation
                    
                    # Handle nested BaseModel
                    if hasattr(field_type, '__bases__') and BaseModel in field_type.__bases__:
                        minimal_response[field_name] = self._create_minimal_response(field_type)
                    elif field_type == str:
                        minimal_response[field_name] = "信息不足，无法回答"
                    elif field_type == int:
                        minimal_response[field_name] = 0
                    elif field_type == float:
                        minimal_response[field_name] = 0.0
                    elif field_type == bool:
                        minimal_response[field_name] = False
                    elif field_type == list:
                        minimal_response[field_name] = []
                    elif field_type == dict:
                        minimal_response[field_name] = {}
                    else:
                        minimal_response[field_name] = None
            
            return response_model.model_validate(minimal_response)
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to create minimal response: {e}")
            # Last resort: try to create with just required fields
            try:
                return response_model()
            except Exception:
                # If even that fails, raise the original error
                raise ValueError(f"Unable to create minimal response for {response_model.__name__}") from e
