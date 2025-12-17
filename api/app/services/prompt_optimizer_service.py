import json
import re
import uuid

from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.models import RedBearModelConfig
from app.core.models.llm import RedBearLLM
from app.models import ModelConfig, ModelApiKey, ModelType, PromptOptimizerSessionHistory
from app.models.prompt_optimizer_model import (
    PromptOptimizerModelConfig,
    PromptOptimizerSession,
    RoleType
)
from app.repositories.model_repository import ModelConfigRepository
from app.repositories.prompt_optimizer_repository import (
    PromptOptimizerModelConfigRepository,
    PromptOptimizerSessionRepository
)
from app.schemas.prompt_optimizer_schema import OptimizePromptResult

logger = get_business_logger()


class PromptOptimizerService:
    def __init__(self, db: Session):
        self.db = db

    def get_model_config(
            self,
            tenant_id: uuid.UUID,
            model_id: uuid.UUID
    ) -> tuple[PromptOptimizerModelConfig, ModelConfig]:
        """
        Retrieve the prompt optimizer model configuration and model configuration.

        This method retrieves the prompt optimizer model configuration associated
        with the specified model ID and tenant. It also fetches the corresponding
        model configuration.

        Args:
            tenant_id (uuid.UUID): The unique identifier of the tenant.
            model_id (uuid.UUID): The unique identifier of the prompt optimization model.

        Returns:
            tuple[PromptOptimzerModelConfig, ModelConfig]:
                A tuple containing the prompt optimizer model configuration
                and the corresponding model configuration.

        Raises:
            BusinessException: If the prompt optimizer model configuration does not exist.
            BusinessException: If the model configuration does not exist.
        """
        prompt_config = PromptOptimizerModelConfigRepository(self.db).get_by_tenant_id(
            tenant_id
        )
        if not prompt_config:
            raise BusinessException("提示词模型配置不存在", BizCode.NOT_FOUND)

        model = ModelConfigRepository.get_by_id(
            self.db, model_id, tenant_id=tenant_id
        )
        if not model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)

        return prompt_config, model

    def create_update_model_config(
            self,
            tenant_id: uuid.UUID,
            config_id: uuid.UUID,
            system_prompt: str,
    ) -> PromptOptimizerModelConfig:
        """
        Create or update a prompt optimizer model configuration.

        This method creates a new prompt optimizer model configuration or updates
        an existing one identified by the given configuration ID. The configuration
        defines the system prompt used for prompt optimization.

        Args:
            tenant_id (uuid.UUID): The unique identifier of the tenant.
            config_id (uuid.UUID): The unique identifier of the configuration to create or update.
            system_prompt (str): The system prompt content used for prompt optimization.

        Returns:
            PromptOptimzerModelConfig: The created or updated prompt optimizer model configuration.
        """
        prompt_config = PromptOptimizerModelConfigRepository(self.db).create_or_update(
            config_id=config_id,
            tenant_id=tenant_id,
            system_prompt=system_prompt,
        )
        return prompt_config

    def create_session(
            self,
            tenant_id: uuid.UUID,
            user_id: uuid.UUID
    ) -> PromptOptimizerSession:
        """
        Create a new prompt optimization session.

        This method initializes a new prompt optimization session for the specified
        tenant, application, and user, and persists it to the database.

        Args:
            tenant_id (uuid.UUID): The unique identifier of the tenant.
            user_id (uuid.UUID): The unique identifier of the user.

        Returns:
            PromptOptimzerSession: The newly created prompt optimization session.
        """
        session = PromptOptimizerSessionRepository(self.db).create_session(
            tenant_id=tenant_id,
            user_id=user_id
        )
        return session

    def get_session_message_history(
            self,
            session_id: uuid.UUID,
            user_id: uuid.UUID
    ) -> list[tuple[str, str]]:
        """
        Retrieve the chronological message history for a prompt optimization session.

        This method queries the database to fetch all messages associated with a
        specific prompt optimization session for a given user. Messages are returned
        in chronological order and typically include both user inputs and
        model-generated responses.

        Args:
            session_id (uuid.UUID): The unique identifier of the prompt optimization session.
            user_id (uuid.UUID): The unique identifier of the user associated with the session.

        Returns:
            list[tuple[str, str]]: A list of tuples representing messages. Each tuple contains:
                - role (str): The role of the message sender, e.g., 'system', 'user', or 'assistant'.
                - content (str): The content of the message.
        """
        history = PromptOptimizerSessionRepository(self.db).get_session_history(
            session_id=session_id,
            user_id=user_id
        )
        messages = []
        for message in history:
            messages.append((message.role, message.content))
        return messages

    async def optimize_prompt(
            self,
            tenant_id: uuid.UUID,
            model_id: uuid.UUID,
            session_id: uuid.UUID,
            user_id: uuid.UUID,
            current_prompt: str,
            message: str
    ) -> OptimizePromptResult:
        """
        Optimize a prompt using a prompt optimizer LLM.

        This method uses a configured prompt optimizer model to refine an existing
        prompt based on the user's requirements. The optimized prompt is generated
        according to predefined system rules, including Jinja2 variable syntax and
        a strict JSON output format.

        Args:
            tenant_id (uuid.UUID): The unique identifier of the tenant.
            model_id (uuid.UUID): The unique identifier of the prompt optimizer model.
            session_id (uuid.UUID): The unique identifier of the prompt optimization session.
            user_id (uuid.UUID): The unique identifier of the user associated with the session.
            current_prompt (str): The original prompt to be optimized.
            message (str): The user's requirements or modification instructions.

        Returns:
            dict: A dictionary containing the optimized prompt and the description
            of changes, in the following format:
            {
                "prompt": "<optimized_prompt>",
                "desc": "<change_description>"
            }

        Raises:
            BusinessException: If the model response cannot be parsed as valid JSON
            or does not conform to the expected output format.
        """
        prompt_config, model_config = self.get_model_config(tenant_id, model_id)
        session_history = self.get_session_message_history(session_id=session_id, user_id=user_id)

        # Create LLM instance
        api_config: ModelApiKey = model_config.api_keys[0]
        llm = RedBearLLM(RedBearModelConfig(
            model_name=api_config.model_name,
            provider=api_config.provider,
            api_key=api_config.api_key,
            base_url=api_config.api_base
        ), type=ModelType.from_str(model_config.type))

        # build message
        messages = [
            # init system_prompt
            (RoleType.SYSTEM.value, prompt_config.system_prompt),

            # base model limit
            (RoleType.SYSTEM.value,
             "Optimization Rules:\n"
             "1. Fully adjust the prompt content according to the user's requirements.\n"
             "2. When the user requests the insertion of variables, you must use Jinja2 syntax {{variable_name}} "
             "(the variable name should be determined based on the user's requirement).\n"
             "3. Keep the prompt logic clear and instructions explicit.\n"
             "4. Ensure that the modified prompt can be directly used.\n\n"
             "Output Requirements:\n"
             "Provide the result in JSON format, containing exactly two fields:\n"
             "  - prompt: The modified prompt (string).\n"
             "  - desc: A response addressing the user's optimization request (string).")
        ]
        messages.extend(session_history[:-1])  # last message is current message
        user_message_template = ChatPromptTemplate.from_messages([
            (RoleType.USER.value, "[current_prompt]\n{current_prompt}\n[user_require]\n{message}")
        ])
        formatted_user_message = user_message_template.format(current_prompt=current_prompt, message=message)
        messages.extend([(RoleType.USER.value, formatted_user_message)])
        logger.info(f"Prompt optimization message: {messages}")
        result = await llm.ainvoke(messages)
        try:
            data_dict = json.loads(result.content)
            model_resp = OptimizePromptResult.model_validate(data_dict)
        except Exception as e:
            logger.error(f"Failed to parse model reponse to json - Error: {str(e)}", exc_info=True)
            raise BusinessException("Failed to parse model response", BizCode.PARSER_NOT_SUPPORTED)
        return model_resp

    @staticmethod
    def parser_prompt_variables(prompt: str):
        try:
            pattern = r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}'
            matches = re.findall(pattern, prompt)
            variables = list(set(matches))
            return variables
        except Exception as e:
            logger.error(f"Failed to parse prompt variables - Error: {str(e)}", exc_info=True)
            raise BusinessException("Failed to parse prompt variables", BizCode.PARSER_NOT_SUPPORTED)

    @staticmethod
    def fill_prompt_variables(prompt: str, variables: dict[str, str]):
        try:
            pattern = r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}'

            def replace_var(match):
                var_name = match.group(1)
                return variables.get(var_name, match.group(0))
            result = re.sub(pattern, replace_var, prompt)
            return result
        except Exception as e:
            logger.error(f"Failed to fill prompt variables - Error: {str(e)}", exc_info=True)
            raise BusinessException("Failed to fill prompt variables", BizCode.PARSER_NOT_SUPPORTED)

    def create_message(
            self,
            tenant_id: uuid.UUID,
            session_id: uuid.UUID,
            user_id: uuid.UUID,
            role: RoleType,
            content: str
    ) -> PromptOptimizerSessionHistory:
        """Insert Message to Session History"""
        message = PromptOptimizerSessionRepository(self.db).create_message(
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=content
        )
        return message

