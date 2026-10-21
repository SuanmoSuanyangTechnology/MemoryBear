# from typing import Optional
# from app.core.model_client import RedBearEmbeddings, RedBearLLM, RedBearRerank, ModelConfig


# class RedBearModelFactory:
#     @staticmethod
#     def llm(model: str, api_key: str, base_url: Optional[str] = None) -> RedBearLLM:
#         return RedBearLLM(ModelConfig(model_name=model, api_key=api_key, base_url=base_url))

#     @staticmethod
#     def embeddings(model: str, api_key: str, base_url: Optional[str] = None) -> RedBearEmbeddings:
#         return RedBearEmbeddings(ModelConfig(model_name=model, api_key=api_key, base_url=base_url))

#     @staticmethod
#     def reranker(model: str, api_key: str, base_url: Optional[str] = None) -> RedBearRerank:
#         return RedBearRerank(ModelConfig(model_name=model, api_key=api_key, base_url=base_url))
