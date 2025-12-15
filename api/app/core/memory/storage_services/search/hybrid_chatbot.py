
# TODO hybrid_chatbot.py 是一个独立的GUI演示应用，不是核心功能的一部分，可以考虑删除
from app.core.memory.utils.llm.llm_utils import get_llm_client
import asyncio
import os
import time
import json
from datetime import datetime, timezone
import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
from typing import Any, Dict, Tuple, List

# Import our hybrid search functionality
from app.core.memory.storage_services.search import run_hybrid_search
# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.models.config_models import LLMConfig
from dotenv import load_dotenv

load_dotenv()


class HybridSearchChatbot:
    def __init__(self):

        from app.core.memory.utils.config import definitions as config_defs
        self.llm_client = get_llm_client(config_defs.SELECTED_LLM_ID)

        # Chat history
        self.chat_history = []

        # Search configuration
        self.search_config = {
            "group_id": "group_wyl_25",
            "limit": 10,
            "include": ["statements", "chunks", "entities","summaries"],
            # "include": ["statements", "dialogues", "entities"],
            "rerank_alpha": 0.6
        }

        # Setup GUI
        self.setup_gui()

    def setup_gui(self):
        """Setup the GUI interface"""
        self.root = tk.Tk()
        self.root.title("Hybrid Search Chatbot")
        self.root.geometry("800x600")

        # Chat display area
        self.chat_display = scrolledtext.ScrolledText(
            self.root,
            wrap=tk.WORD,
            width=80,
            height=25,
            state=tk.DISABLED
        )
        self.chat_display.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        # Input frame
        input_frame = tk.Frame(self.root)
        input_frame.pack(padx=10, pady=5, fill=tk.X)

        # User input
        self.user_input = tk.Entry(input_frame, font=("Arial", 12))
        self.user_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.user_input.bind("<Return>", self.on_send_message)

        # Send button
        self.send_button = tk.Button(
            input_frame,
            text="发送",
            command=self.on_send_message,
            font=("Arial", 12)
        )
        self.send_button.pack(side=tk.RIGHT)

        # Status frame
        status_frame = tk.Frame(self.root)
        status_frame.pack(padx=10, pady=5, fill=tk.X)

        # Status label
        self.status_label = tk.Label(
            status_frame,
            text="就绪",
            font=("Arial", 10),
            anchor="w"
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Search config button
        config_button = tk.Button(
            status_frame,
            text="搜索配置",
            command=self.show_config_dialog,
            font=("Arial", 10)
        )
        config_button.pack(side=tk.RIGHT)

        # Add welcome message
        self.add_message("系统", "欢迎使用混合搜索聊天机器人！我可以基于知识图谱中的信息回答您的问题。")

    def add_message(self, sender: str, message: str, metadata: Dict = None):
        """Add a message to the chat display"""
        self.chat_display.config(state=tk.NORMAL)

        timestamp = datetime.now().strftime("%H:%M:%S")

        # Add sender and timestamp
        self.chat_display.insert(tk.END, f"[{timestamp}] {sender}:\n", "sender")

        # Add message content
        self.chat_display.insert(tk.END, f"{message}\n", "message")

        # Add metadata if available
        if metadata:
            self.chat_display.insert(tk.END, f" {metadata}\n", "metadata")

        self.chat_display.insert(tk.END, "\n")
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)

        # Configure text tags for styling
        self.chat_display.tag_config("sender", foreground="blue", font=("Arial", 10, "bold"))
        self.chat_display.tag_config("message", foreground="black", font=("Arial", 10))
        self.chat_display.tag_config("metadata", foreground="gray", font=("Arial", 8))

    def show_config_dialog(self):
        """Show search configuration dialog"""
        config_window = tk.Toplevel(self.root)
        config_window.title("搜索配置")
        config_window.geometry("400x600")
        config_window.transient(self.root)
        config_window.grab_set()

        # Current configuration display
        current_config_frame = tk.Frame(config_window)
        current_config_frame.pack(pady=10, padx=10, fill=tk.X)
        tk.Label(current_config_frame, text="当前配置:", font=("Arial", 10, "bold")).pack(anchor="w")
        current_text = f"Alpha: {self.search_config['rerank_alpha']}, 限制: {self.search_config['limit']}, 目标: {', '.join(self.search_config['include'])}"
        tk.Label(current_config_frame, text=current_text, font=("Arial", 9), fg="blue").pack(anchor="w")

        # Alpha parameter
        tk.Label(config_window, text="重排权重 (Alpha):").pack(pady=(10, 5))
        alpha_var = tk.DoubleVar(value=self.search_config["rerank_alpha"])
        alpha_scale = tk.Scale(
            config_window,
            from_=0.0,
            to=1.0,
            resolution=0.1,
            orient=tk.HORIZONTAL,
            variable=alpha_var
        )
        alpha_scale.pack(pady=5, padx=20, fill=tk.X)
        tk.Label(config_window, text="0.0=纯语义搜索, 1.0=纯关键词搜索", font=("Arial", 8)).pack()

        # Limit parameter
        tk.Label(config_window, text="搜索结果数量:").pack(pady=(20, 5))
        limit_var = tk.IntVar(value=self.search_config["limit"])
        limit_spinbox = tk.Spinbox(
            config_window,
            from_=1,
            to=50,
            textvariable=limit_var,
            width=10
        )
        limit_spinbox.pack(pady=5)

        # Include options
        tk.Label(config_window, text="搜索目标:").pack(pady=(20, 5))
        include_frame = tk.Frame(config_window)
        include_frame.pack(pady=5)

        include_vars = {}
        for option in ["statements", "chunks", "entities","summaries"]:
            var = tk.BooleanVar(value=option in self.search_config["include"])
            include_vars[option] = var
            tk.Checkbutton(
                include_frame,
                text=option,
                variable=var
            ).pack(side=tk.LEFT, padx=10)

        # Buttons
        button_frame = tk.Frame(config_window)
        button_frame.pack(pady=20)

        def save_config():
            try:
                # Validate inputs
                alpha_value = alpha_var.get()
                limit_value = limit_var.get()
                include_list = [
                    option for option, var in include_vars.items() if var.get()
                ]

                # Check if at least one search target is selected
                if not include_list:
                    messagebox.showerror("配置错误", "请至少选择一个搜索目标！")
                    return

                # Update configuration
                self.search_config["rerank_alpha"] = alpha_value
                self.search_config["limit"] = limit_value
                self.search_config["include"] = include_list

                config_window.destroy()
                self.add_message("系统",
                                 f"配置已更新: Alpha={alpha_value:.1f}, 限制={limit_value}, 目标={', '.join(include_list)}")

            except Exception as e:
                messagebox.showerror("配置错误", f"保存配置时出错: {str(e)}")
                print(f"Config save error: {e}")  # Debug output

        tk.Button(button_frame, text="保存", command=save_config).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="取消", command=config_window.destroy).pack(side=tk.LEFT, padx=5)

    def on_send_message(self, event=None):
        """Handle sending a message"""
        user_message = self.user_input.get().strip()
        if not user_message:
            return

        # Clear input
        self.user_input.delete(0, tk.END)

        # Add user message to display
        self.add_message("用户", user_message)

        # Disable send button and show processing status
        self.send_button.config(state=tk.DISABLED)
        self.status_label.config(text="正在搜索和生成回复...")

        # Process message in background thread
        threading.Thread(
            target=self.process_message_async,
            args=(user_message,),
            daemon=True
        ).start()

    def process_message_async(self, user_message: str):
        """Process message asynchronously"""
        try:
            # Run the async processing
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            response, metadata = loop.run_until_complete(
                self.process_message(user_message)
            )
            loop.close()

            # Update GUI in main thread
            self.root.after(0, self.on_response_ready, response, metadata)

        except Exception as e:
            error_msg = f"处理消息时出错: {str(e)}"
            self.root.after(0, self.on_error, error_msg)

    async def process_message(self, user_message: str) -> Tuple[str, Dict[str, Any]]:
        """Process user message with hybrid search"""
        start_time = time.time()

        # Perform hybrid search
        search_start = time.time()
        search_results = await run_hybrid_search(
            query_text=user_message,
            search_type="hybrid",
            group_id=self.search_config["group_id"],
            limit=self.search_config["limit"],
            include=self.search_config["include"],
            output_path=None,
            rerank_alpha=self.search_config["rerank_alpha"]
        )
        search_time = time.time() - search_start

        # Extract relevant information from search results
        context_info = self.extract_context_from_search(search_results)

        # Generate response using LLM
        llm_start = time.time()
        response = await self.generate_response(user_message, context_info)
        llm_time = time.time() - llm_start

        total_time = time.time() - start_time

        # Prepare metadata
        metadata = {
            "搜索时间": f"{search_time:.2f}s",
            "生成时间": f"{llm_time:.2f}s",
            "总时间": f"{total_time:.2f}s",
            "搜索结果": self.get_search_summary(search_results),
            "重排权重": self.search_config["rerank_alpha"]
        }

        return response, metadata

    def extract_context_from_search(self, search_results: Dict) -> str:
        """Extract context information from search results"""
        if not search_results:
            return "未找到相关信息。"

        context_parts = []

        # Get reranked results if available, otherwise use individual results
        if "reranked_results" in search_results:
            results = search_results["reranked_results"]
        else:
            results = {}
            for key in ["keyword_search", "embedding_search"]:
                if key in search_results:
                    for category, items in search_results[key].items():
                        if category not in results:
                            results[category] = []
                        results[category].extend(items)

        # Extract statements
        if "statements" in results and results["statements"]:
            statements = results["statements"][:5]  # Top 5
            context_parts.append("相关陈述:")
            for i, stmt in enumerate(statements, 1):
                content = stmt.get("statement", "")
                score = stmt.get("combined_score", stmt.get("score", 0))
                context_parts.append(f"{i}. {content} (相关度: {score:.3f})")

        # Extract chunks
        if "chunks" in results and results["chunks"]:
            chunks = results["chunks"][:3]  # Top 3
            context_parts.append("\n相关对话:")
            for i, chunk in enumerate(chunks, 1):
                content = chunk.get("content", "")
                score = chunk.get("combined_score", chunk.get("score", 0))
                context_parts.append(f"{i}. {content} (相关度: {score:.3f})")

        # Extract entities
        if "entities" in results and results["entities"]:
            entities = results["entities"][:5]  # Top 5
            context_parts.append("\n相关实体:")
            entity_names = [ent.get("name", "") for ent in entities]
            context_parts.append(", ".join(entity_names))

        return "\n".join(context_parts) if context_parts else "未找到相关信息。"

    def get_search_summary(self, search_results: Dict) -> str:
        """Get a summary of search results"""
        if not search_results:
            return "无结果"

        summary_parts = []

        if "combined_summary" in search_results:
            summary = search_results["combined_summary"]
            if "total_reranked_results" in summary:
                summary_parts.append(f"重排结果: {summary['total_reranked_results']}")
            if "total_keyword_results" in summary:
                summary_parts.append(f"关键词: {summary['total_keyword_results']}")
            if "total_embedding_results" in summary:
                summary_parts.append(f"语义: {summary['total_embedding_results']}")

        return ", ".join(summary_parts) if summary_parts else "有结果"

    async def generate_response(self, user_message: str, context: str) -> str:
        """Generate response using LLM"""
        system_prompt = f"""你是一个智能助手，基于知识图谱中的信息回答用户问题。

以下是从知识图谱中检索到的相关信息：
{context}

请基于这些信息回答用户的问题。如果信息不足，请诚实地说明。回答要自然、友好，并且准确。"""

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]

            response = self.llm_client.chat(
                messages=messages,
            )
            print(response)
            # Extract content from various possible response types
            # 1) LangChain AIMessage or similar object with `.content`
            if hasattr(response, 'content'):
                return getattr(response, 'content')

            # 2) OpenAI-style response with `.choices`
            if hasattr(response, 'choices') and response.choices:
                first_choice = response.choices[0]
                # Newer clients may have `.message.content`, some have `.content` directly
                if hasattr(first_choice, 'message') and hasattr(first_choice.message, 'content'):
                    return first_choice.message.content
                if hasattr(first_choice, 'content'):
                    return first_choice.content

            # 3) Dict-like responses
            if isinstance(response, dict):
                if 'content' in response:
                    return response['content']
                if 'choices' in response and response['choices']:
                    ch = response['choices'][0]
                    if isinstance(ch, dict):
                        if 'message' in ch and 'content' in ch['message']:
                            return ch['message']['content']
                        if 'content' in ch:
                            return ch['content']

            # 4) Fallback: if it's a plain string
            if isinstance(response, str):
                return response

            # Default fallback
            return "抱歉，我无法生成回复。"

        except Exception as e:
            return f"生成回复时出错: {str(e)}"

    def on_response_ready(self, response: str, metadata: Dict[str, Any]):
        """Handle when response is ready"""
        self.add_message("助手", response, metadata)
        self.send_button.config(state=tk.NORMAL)
        self.status_label.config(text="就绪")
        self.user_input.focus()

    def on_error(self, error_message: str):
        """Handle errors"""
        self.add_message("系统", f" {error_message}")
        self.send_button.config(state=tk.NORMAL)
        self.status_label.config(text="就绪")
        self.user_input.focus()

    def run(self):
        """Start the chatbot"""
        self.root.mainloop()


def main():
    """Main function to run the chatbot"""
    try:
        chatbot = HybridSearchChatbot()
        chatbot.run()
    except Exception as e:
        print(f"启动聊天机器人时出错: {e}")


if __name__ == "__main__":
    main()
