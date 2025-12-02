
# from typing import Any, Dict, List, Optional
# from langchain_core.runnables import RunnableSerializable

# from app.core.models.base import RedBearModelConfig

# class RedBearRerank(RunnableSerializable[str, List[float]]):
#     """ Rerank → 作为 Runnable 插入任意 LCEL 链"""
#     def __init__(self, config: RedBearModelConfig):
#         super().__init__(self, config)

#     def invoke(self, input: Dict[str, Any], config: Optional[Dict] = None) -> List[float]:
#         query, docs = input["query"], input["documents"]
#         url = (self.config.base_url or "https://api.cohere.ai/v1") + "/rerank"
#         body = {
#             "query": query,
#             "documents": docs,
#             "model": self.config.model_name,
#             "top_n": len(docs),
#         }
#         js = self._sync_post(url, body)
#         scores = [0.0] * len(docs)
#         for item in js["results"]:
#             scores[item["index"]] = item["relevance_score"]
#         return scores

#     async def ainvoke(self, input: Dict[str, Any], config: Optional[Dict] = None) -> List[float]:
#         query, docs = input["query"], input["documents"]
#         url = (self.config.base_url or "https://api.cohere.ai/v1") + "/rerank"
#         body = {"query": query, "documents": docs, "model": self.config.model_name, "top_n": len(docs)}
#         js = await self._async_post(url, body)
#         scores = [0.0] * len(docs)
#         for item in js["results"]:
#             scores[item["index"]] = item["relevance_score"]
#         return scores