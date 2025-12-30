from app.core.rag.utils import es_conn
from app.core.rag.nlp import search
from app.core.rag.graphrag import search as kg_search

PARALLEL_DEVICES: int = 0

docStoreConn = None

retriever = None
kg_retriever = None


def init_settings():
    global docStoreConn, retriever, kg_retriever

    if docStoreConn is None:
        docStoreConn = es_conn.ESConnection()
    if retriever is None:
        retriever = search.Dealer(docStoreConn)
    if kg_retriever is None:
        kg_retriever = kg_search.KGSearch(docStoreConn)


init_settings()