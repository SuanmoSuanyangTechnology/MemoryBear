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
    PromptOptimizerSession,
    RoleType
)
from app.repositories.model_repository import ModelConfigRepository
from app.repositories.prompt_optimizer_repository import (
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
    ) -> ModelConfig:
        """
        Retrieve the model configuration for a specific tenant.

        This method fetches the model configuration associated with the given
        tenant_id and model_id. If no configuration is found, a BusinessException
        is raised.

        Args:
            tenant_id (uuid.UUID): The unique identifier of the tenant.
            model_id (uuid.UUID): The unique identifier of the model.

        Returns:
            ModelConfig: The corresponding model configuration object.

        Raises:
            BusinessException: If the model configuration does not exist.
        """

        model = ModelConfigRepository.get_by_id(
            self.db, model_id, tenant_id=tenant_id
        )
        if not model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)

        return model

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
            user_require: str
    ) -> OptimizePromptResult:
        """
        Optimize a user-provided prompt using a configured prompt optimizer LLM.

        This method refines the original prompt according to the user's requirements,
        generating an optimized version that is directly usable by AI tools. The
        optimization process follows strict rules, including:
        - Wrapping user-inserted variables in double curly braces {{}}.
        - Adhering to Jinja2 variable syntax if applicable.
        - Ensuring a clear logic flow, explicit instructions, and strong executability.
        - Producing output in a strict JSON format.

        Steps performed:
        1. Retrieve the model configuration for the given tenant and model.
        2. Fetch the session message history for context.
        3. Instantiate the LLM with the appropriate API key and model configuration.
        4. Build system messages outlining optimization rules.
        5. Format the user's original prompt and requirements as a user message.
        6. Send messages to the LLM to generate the optimized prompt.
        7. Generate a concise description summarizing the changes made during optimization.

        Args:
            tenant_id (uuid.UUID): Tenant identifier.
            model_id (uuid.UUID): Prompt optimizer model identifier.
            session_id (uuid.UUID): Prompt optimization session identifier.
            user_id (uuid.UUID): Identifier of the user associated with the session.
            current_prompt (str): Original prompt to optimize.
            user_require (str): User's requirements or instructions for optimization.

        Returns:
            OptimizePromptResult: An object containing:
                - prompt: The optimized prompt string.
                - desc: A short description summarizing the changes.

        Raises:
            BusinessException: If the LLM response cannot be parsed as valid JSON
            or does not conform to the expected output format.
        """
        model_config = self.get_model_config(tenant_id, model_id)
        session_history = self.get_session_message_history(session_id=session_id, user_id=user_id)

        # Create LLM instance
        api_config: ModelApiKey = model_config.api_keys[0]
        llm = RedBearLLM(RedBearModelConfig(
            model_name=api_config.model_name,
            provider=api_config.provider,
            api_key=api_config.api_key,
            base_url=api_config.api_base
        ), type=ModelType(model_config.type))

        # build message
        messages = [
            # init system_prompt
            (
                RoleType.SYSTEM.value,
                "Your task is to optimize the original prompt provided by the user so that it can be directly used by AI tools,"
                "and the variables that the user needs to insert must be wrapped in {{}}. "
                "The optimized prompt should align with the optimization direction specified by the user (if any) and ensure clear logic, explicit instructions, and strong executability. "
                "Please follow these rules when optimizing: "
                '1. Ensure variables are wrapped in {{}}, e.g., optimize "Please enter your question" to "Please enter your {{question}}"'
                "2. Instructions must be specific and operable, avoiding vague expressions"
                "3. If the original prompt lacks key elements (such as output format requirements), supplement them completely "
                "4. Keep the language concise and avoid redundancy "
                "5. If the user does not specify an optimization direction, the default optimization is to make the prompt structurally clear and with explicit instructions"
                "Please directly output the optimized prompt without additional explanations. The optimized prompt should be directly usable with correct variable positions."
            ),

            # base model limit
            (RoleType.SYSTEM.value,
             "Optimization Rules:\n"
             "1. Fully adjust the prompt content according to the user's requirements.\n"
             "When variables are required, use double curly braces {{variable_name}} as placeholders."
             "Variable names must be derived from the user's requirements.\n"
             "3. Keep the prompt logic clear and instructions explicit.\n"
             "4. Ensure that the modified prompt can be directly used.\n\n")
        ]
        messages.extend(session_history[:-1])  # last message is current message
        user_message_template = ChatPromptTemplate.from_messages([
            (RoleType.USER.value, "[original_prompt]\n{current_prompt}\n[user_require]\n{user_require}")
        ])
        formatted_user_message = user_message_template.format(current_prompt=current_prompt, user_require=user_require)
        messages.extend([(RoleType.USER.value, formatted_user_message)])
        logger.info(f"Prompt optimization message: {messages}")
        optim_prompt = await llm.ainvoke(messages)
        optim_desc = [
            (
                RoleType.SYSTEM.value,
                "You are a prompt optimization assistant.\n"
                "Compare the original prompt, the user's requirements, "
                "and the optimized prompt.\n"
                "Summarize the changes made during optimization.\n\n"
                "Rules:\n"
                "1. Output must be a single short sentence.\n"
                "2. Be concise and factual.\n"
                "3. Do not explain the prompts themselves.\n"
                "4. Do not include any extra text."
            ),
            (
                "[Original Prompt]\n"
                f"{current_prompt}\n\n"
                "[User Requirements]\n"
                f"{user_require}\n\n"
                "[Optimized Prompt]\n"
                f"{optim_prompt.content}"
            )
        ]
        optim_desc = await llm.ainvoke(optim_desc)

        return OptimizePromptResult(
            prompt=optim_prompt.content,
            desc=optim_desc.content
        )

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
