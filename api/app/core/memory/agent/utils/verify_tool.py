import asyncio
import json
import os
from typing import Annotated, Any, List, TypedDict

# Removed global variable imports - use dependency injection instead
from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
from app.core.memory.agent.utils.messages_tool import _to_openai_messages
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from dotenv import find_dotenv, load_dotenv
from jinja2 import Environment, FileSystemLoader
from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.constants import END, START
from langgraph.graph import StateGraph, add_messages

load_dotenv(find_dotenv())

logger = get_agent_logger(__name__)

def keep_last(_, right):
    return right
class State(TypedDict):
    user_input: Annotated[dict, keep_last]
    messages: Annotated[List[AnyMessage], add_messages]
    agent1_response: str
    agent2_response: str
    agent3_response: str
    final_response: str
    status: Annotated[str, keep_last]


class VerifyTool:
    def __init__(self, system_prompt: str="", verify_data: Any=None, llm_model_id: str=None):
        """
        Updated to eliminate global variables in favor of explicit parameters.
        
        Args:
            system_prompt: System prompt for verification
            verify_data: Data to verify
            llm_model_id: LLM model ID (required, no longer from global variables)
        """
        self.system_prompt = system_prompt
        self.llm_model_id = llm_model_id
        if isinstance(verify_data, str):
            self.verify_data = verify_data
        else:
            try:
                self.verify_data = json.dumps(verify_data, ensure_ascii=False)
            except Exception:
                self.verify_data = str(verify_data)

    async def model_1(self, state: State) -> State:
        if not self.llm_model_id:
            raise ValueError("llm_model_id is required but not provided")
        with get_db_context() as db:
            factory = MemoryClientFactory(db)
            llm_client = factory.get_llm_client(self.llm_model_id)
        response_content = await llm_client.chat(
            messages=[{"role": "system", "content": self.system_prompt}, *_to_openai_messages(state["messages"])]
        )
        return {
            "agent1_response": response_content,
            "status": "processed",
        }


    def get_graph(self):
        graph = StateGraph(State)
        graph.add_node("model_1", self.model_1)

        graph.add_edge(START, "model_1")
        graph.add_edge("model_1", END)

        compiled_graph = graph.compile()
        return compiled_graph

    async def verify(self):
        graph = self.get_graph()
        initial_state = {
            "user_input": self.verify_data,
            "messages": [HumanMessage(content=self.verify_data)],
            "final_response": "",
            "status": ""
        }
        final_state = await graph.ainvoke(initial_state)
        # return final_state["final_response"]
        return final_state["agent1_response"]

