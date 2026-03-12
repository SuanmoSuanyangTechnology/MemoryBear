#!/usr/bin/env python3
from contextlib import asynccontextmanager

from langchain_core.messages import HumanMessage
from langgraph.constants import START, END
from langgraph.graph import StateGraph

from app.db import get_db
from app.services.memory_config_service import MemoryConfigService

from app.core.memory.agent.utils.llm_tools import ReadState
from app.core.memory.agent.langgraph_graph.nodes.data_nodes import content_input_node
from app.core.memory.agent.langgraph_graph.nodes.problem_nodes import (
    Split_The_Problem,
    Problem_Extension,
)
from app.core.memory.agent.langgraph_graph.nodes.retrieve_nodes import (
    retrieve,
)
from app.core.memory.agent.langgraph_graph.nodes.summary_nodes import (
    Input_Summary,
    Retrieve_Summary,
    Summary_fails,
    Summary,
)
from app.core.memory.agent.langgraph_graph.nodes.verification_nodes import Verify
from app.core.memory.agent.langgraph_graph.routing.routers import (
    Split_continue,
    Retrieve_continue,
    Verify_continue,
)


@asynccontextmanager
async def make_read_graph():
    """
    Create and return a LangGraph workflow for memory reading operations
    
    Builds a state graph workflow that handles memory retrieval, problem analysis,
    verification, and summarization. The workflow includes nodes for content input,
    problem splitting, retrieval, verification, and various summary operations.
    
    Yields:
        StateGraph: Compiled LangGraph workflow for memory reading
        
    Raises:
        Exception: If workflow creation fails
    """
    try:
        # Build workflow graph
        workflow = StateGraph(ReadState)
        workflow.add_node("content_input", content_input_node)
        workflow.add_node("Split_The_Problem", Split_The_Problem)
        workflow.add_node("Problem_Extension", Problem_Extension)
        workflow.add_node("Input_Summary", Input_Summary)
        # workflow.add_node("Retrieve", retrieve_nodes)
        workflow.add_node("Retrieve", retrieve)
        workflow.add_node("Verify", Verify)
        workflow.add_node("Retrieve_Summary", Retrieve_Summary)
        workflow.add_node("Summary", Summary)
        workflow.add_node("Summary_fails", Summary_fails)

        # Add edges to define workflow flow
        workflow.add_edge(START, "content_input")
        workflow.add_conditional_edges("content_input", Split_continue)
        workflow.add_edge("Input_Summary", END)
        workflow.add_edge("Split_The_Problem", "Problem_Extension")
        workflow.add_edge("Problem_Extension", "Retrieve")
        workflow.add_conditional_edges("Retrieve", Retrieve_continue)
        workflow.add_edge("Retrieve_Summary", END)
        workflow.add_conditional_edges("Verify", Verify_continue)
        workflow.add_edge("Summary_fails", END)
        workflow.add_edge("Summary", END)

        '''-----'''
        # workflow.add_edge("Retrieve", END)

        # Compile workflow
        graph = workflow.compile()
        yield graph

    except Exception as e:
        print(f"创建工作流失败: {e}")
        raise
    finally:
        print("工作流创建完成")
