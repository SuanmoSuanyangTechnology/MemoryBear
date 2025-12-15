from typing import TypedDict, Annotated, List, Any
from langchain_core.messages import AnyMessage
from langgraph.constants import START, END
from langgraph.graph import StateGraph, add_messages
import asyncio
import json
from dotenv import load_dotenv, find_dotenv
import os
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
from langchain_core.messages import HumanMessage
from jinja2 import Environment, FileSystemLoader
from app.core.memory.agent.utils.messages_tool import _to_openai_messages
from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.core.memory.utils.config.definitions import SELECTED_LLM_ID
from app.core.logging_config import get_agent_logger

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
    def __init__(self, system_prompt: str="", verify_data: Any=None):
        self.system_prompt = system_prompt
        if isinstance(verify_data, str):
            self.verify_data = verify_data
        else:
            try:
                self.verify_data = json.dumps(verify_data, ensure_ascii=False)
            except Exception:
                self.verify_data = str(verify_data)

    async def model_1(self, state: State) -> State:
        llm_client = get_llm_client(SELECTED_LLM_ID)
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

