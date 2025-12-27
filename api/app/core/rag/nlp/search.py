import json
import logging
import re
import math
import os
from collections import OrderedDict
from dataclasses import dataclass
import uuid
from typing import Dict, List, Any
import numpy as np
from sqlalchemy.orm import Session
from langchain_core.documents import Document

from app.db import get_db
from app.core.models.base import RedBearModelConfig
from app.core.models import RedBearLLM, RedBearRerank
from app.models.models_model import ModelApiKey
from app.models import knowledge_model
from app.core.rag.models.chunk import DocumentChunk
from app.repositories import knowledge_repository, knowledgeshare_repository
from app.services.model_service import ModelConfigService
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.rag.prompts.generator import relevant_chunks_with_toc
from app.core.rag.nlp import rag_tokenizer, query
from app.core.rag.utils.doc_store_conn import DocStoreConnection, MatchDenseExpr, FusionExpr, OrderByExpr
from app.core.rag.common.string_utils import remove_redundant_spaces
from app.core.rag.common.float_utils import get_float
from app.core.rag.common.constants import PAGERANK_FLD, TAG_FLD


def knowledge_retrieval(
        query: str,
        config: Dict[str, Any],
        user_ids: List[str] = None,
) -> list[DocumentChunk]:
    """
    Knowledge retrieval with multiple knowledge bases and reranking

    Args:
        query: Search query string
        config: Configuration dictionary containing:
            - knowledge_bases: List of knowledge base configs with:
                - kb_id: Knowledge base ID
                - similarity_threshold: float
                - vector_similarity_weight: float
                - top_k: int
                - retrieve_type: "participle" or "semantic" or "hybrid"
            - merge_strategy: "weight" or other strategies
            - reranker_id: UUID of the reranker to use
            - reranker_top_k: int

    Returns:
        Rearranged document block list (in descending order of relevance)
    """
    db = next(get_db())  # Manually call the generator
    try:
        # parse configuration
        knowledge_bases = config.get("knowledge_bases", [])
        merge_strategy = config.get("merge_strategy", "weight")
        reranker_id = config.get("reranker_id")
        reranker_top_k = config.get("reranker_top_k", 1024)

        file_names_filter = []
        if user_ids:
            file_names_filter.extend([f"{user_id}.txt" for user_id in user_ids])

        if not knowledge_bases:
            return []

        all_results = []
        # Search each knowledge base
        for kb_config in knowledge_bases:
            kb_id = kb_config["kb_id"]
            try:
                # Check whether the knowledge base exists and is available
                db_knowledge = knowledge_repository.get_knowledge_by_id(db, knowledge_id=kb_id)
                if db_knowledge and db_knowledge.chunk_num > 0 and db_knowledge.status == 1:
                    # Process shared knowledge base
                    if db_knowledge.permission_id.lower() == knowledge_model.PermissionType.Share:
                        knowledgeshare = knowledgeshare_repository.get_knowledgeshare_by_id(db=db,
                                                                                            knowledgeshare_id=db_knowledge.id)
                        if knowledgeshare:
                            db_knowledge = knowledge_repository.get_knowledge_by_id(db,
                                                                                    knowledge_id=knowledgeshare.source_kb_id)
                            if not (db_knowledge and db_knowledge.chunk_num > 0 and db_knowledge.status == 1):
                                continue
                        else:
                            continue

                    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
                    # Retrieve according to the configured retrieval type
                    match kb_config["retrieve_type"]:
                        case "participle":
                            rs = vector_service.search_by_full_text(
                                query=query,
                                top_k=kb_config["top_k"],
                                score_threshold=kb_config["similarity_threshold"],
                                file_names_filter=file_names_filter
                            )
                        case "semantic":
                            rs = vector_service.search_by_vector(
                                query=query,
                                top_k=kb_config["top_k"],
                                score_threshold=kb_config["vector_similarity_weight"],
                                file_names_filter=file_names_filter
                            )
                        case _:  # hybrid
                            rs1 = vector_service.search_by_vector(
                                query=query,
                                top_k=kb_config["top_k"],
                                score_threshold=kb_config["vector_similarity_weight"],
                                file_names_filter=file_names_filter
                            )
                            rs2 = vector_service.search_by_full_text(
                                query=query,
                                top_k=kb_config["top_k"],
                                score_threshold=kb_config["similarity_threshold"],
                                file_names_filter=file_names_filter
                            )

                            # Deduplication of merge results
                            seen_ids = set()
                            unique_rs = []
                            for doc in rs1 + rs2:
                                if doc.metadata["doc_id"] not in seen_ids:
                                    seen_ids.add(doc.metadata["doc_id"])
                                    unique_rs.append(doc)
                            rs = unique_rs

                    all_results.extend(rs)
            except Exception as e:
                # Failure of retrieval in a single knowledge base does not affect other knowledge bases
                print(f"retrieval knowledge({kb_id}) failed: {str(e)}")
                continue

        # Use the specified reranker for re-ranking
        if reranker_id:
            return rerank(db=db, reranker_id=reranker_id, query=query, docs=all_results, top_k=reranker_top_k)
        return all_results

    except Exception as e:
        print(f"retrieval knowledge failed: {str(e)}")
    finally:
        db.close()


def rerank(db: Session, reranker_id: uuid, query: str, docs: list[DocumentChunk], top_k: int) -> list[DocumentChunk]:
    """
    Reorder the list of document blocks and return the top_k results most relevant to the query
    Args:
        reranker_id: reranker model id
        query: query string
        docs: List of document blocks to be rearranged
        top_k: Number of top-level documents returned

    Returns:
        Rearranged document block list (in descending order of relevance)

    Raises:
        ValueError: If the input document list is empty or top_k is invalid
    """
    # 参数校验
    if not reranker_id:
        raise ValueError("reranker_id be empty")
    if not docs:
        raise ValueError("retrieval chunks be empty")
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    try:
        # initialize reranker
        config = ModelConfigService.get_model_by_id(db=db, model_id=reranker_id)
        apiConfig: ModelApiKey = config.api_keys[0]
        reranker = RedBearRerank(RedBearModelConfig(
            model_name=apiConfig.model_name,
            provider=apiConfig.provider,
            api_key=apiConfig.api_key,
            base_url=apiConfig.api_base
        ))
        # Convert to LangChain Document object
        documents = [
            Document(
                page_content=doc.page_content,  # Ensure that DocumentChunk possesses this attribute
                metadata=doc.metadata or {}  # Deal with possible None metadata
            )
            for doc in docs
        ]

        # Perform reordering (compress_documents will automatically handle relevance scores and indexing)
        reranked_docs = list(reranker.compress_documents(documents, query))
        print(reranked_docs)

        # Sort in descending order based on relevance score
        reranked_docs.sort(
            key=lambda x: x.metadata.get("relevance_score", 0),
            reverse=True
        )
        # Convert back to a list of DocumentChunk, and save the relevance_score to metadata["score"]
        result = []
        for item in reranked_docs[:top_k]:
            for doc in docs:
                if doc.page_content == item.page_content:
                    doc.metadata["score"] = item.metadata["relevance_score"]
                    result.append(doc)
        return result
    except Exception as e:
        raise RuntimeError(f"Failed to rerank documents: {str(e)}") from e


def index_name(uid): return f"graphrag_{uid}"


class Dealer:
    def __init__(self, dataStore: DocStoreConnection):
        self.qryr = query.FulltextQueryer()
        self.dataStore = dataStore

    @dataclass
    class SearchResult:
        total: int
        ids: list[str]
        query_vector: list[float] | None = None
        field: dict | None = None
        highlight: dict | None = None
        aggregation: list | dict | None = None
        keywords: list[str] | None = None
        group_docs: list[list] | None = None

    def get_vector(self, txt, emb_mdl, topk=10, similarity=0.1):
        qv, _ = emb_mdl.encode_queries(txt)
        shape = np.array(qv).shape
        if len(shape) > 1:
            raise Exception(
                f"Dealer.get_vector returned array's shape {shape} doesn't match expectation(exact one dimension).")
        embedding_data = [get_float(v) for v in qv]
        vector_column_name = f"q_{len(embedding_data)}_vec"
        return MatchDenseExpr(vector_column_name, embedding_data, 'float', 'cosine', topk, {"similarity": similarity})

    def get_filters(self, req):
        condition = dict()
        for key, field in {"kb_ids": "kb_id", "document_ids": "document_id"}.items():
            if key in req and req[key] is not None:
                condition[field] = req[key]
        # TODO(yzc): `available_int` is nullable however infinity doesn't support nullable columns.
        for key in ["knowledge_graph_kwd", "available_int", "entity_kwd", "from_entity_kwd", "to_entity_kwd",
                    "removed_kwd"]:
            if key in req and req[key] is not None:
                condition[key] = req[key]
        return condition

    def search(self, req, idx_names: str | list[str],
               kb_ids: list[str],
               emb_mdl=None,
               highlight: bool | list | None = None,
               rank_feature: dict | None = None
               ):
        if highlight is None:
            highlight = False

        filters = self.get_filters(req)
        orderBy = OrderByExpr()

        pg = int(req.get("page", 1)) - 1
        topk = int(req.get("topk", 1024))
        ps = int(req.get("size", topk))
        offset, limit = pg * ps, ps

        src = req.get("fields",
                      ["docnm_kwd", "content_ltks", "kb_id", "img_id", "title_tks", "important_kwd", "position_int",
                       "document_id", "page_num_int", "top_int", "create_timestamp_flt", "knowledge_graph_kwd",
                       "question_kwd", "question_tks", "doc_type_kwd",
                       "available_int", "page_content", PAGERANK_FLD, TAG_FLD])
        kwds = set([])

        qst = req.get("question", "")
        q_vec = []
        if not qst:
            if req.get("sort"):
                orderBy.asc("page_num_int")
                orderBy.asc("top_int")
                orderBy.desc("create_timestamp_flt")
            res = self.dataStore.search(src, [], filters, [], orderBy, offset, limit, idx_names, kb_ids)
            total = self.dataStore.getTotal(res)
            logging.debug("Dealer.search TOTAL: {}".format(total))
        else:
            highlightFields = ["content_ltks", "title_tks"]
            if not highlight:
                highlightFields = []
            elif isinstance(highlight, list):
                highlightFields = highlight
            matchText, keywords = self.qryr.question(qst, min_match=0.3)
            if emb_mdl is None:
                matchExprs = [matchText]
                res = self.dataStore.search(src, highlightFields, filters, matchExprs, orderBy, offset, limit,
                                            idx_names, kb_ids, rank_feature=rank_feature)
                total = self.dataStore.getTotal(res)
                logging.debug("Dealer.search TOTAL: {}".format(total))
            else:
                matchDense = self.get_vector(qst, emb_mdl, topk, req.get("similarity", 0.1))
                q_vec = matchDense.embedding_data
                src.append(f"q_{len(q_vec)}_vec")

                fusionExpr = FusionExpr("weighted_sum", topk, {"weights": "0.05,0.95"})
                matchExprs = [matchText, matchDense, fusionExpr]

                res = self.dataStore.search(src, highlightFields, filters, matchExprs, orderBy, offset, limit,
                                            idx_names, kb_ids, rank_feature=rank_feature)
                total = self.dataStore.getTotal(res)
                logging.debug("Dealer.search TOTAL: {}".format(total))

                # If result is empty, try again with lower min_match
                if total == 0:
                    if filters.get("document_id"):
                        res = self.dataStore.search(src, [], filters, [], orderBy, offset, limit, idx_names, kb_ids)
                        total = self.dataStore.getTotal(res)
                    else:
                        matchText, _ = self.qryr.question(qst, min_match=0.1)
                        matchDense.extra_options["similarity"] = 0.17
                        res = self.dataStore.search(src, highlightFields, filters, [matchText, matchDense, fusionExpr],
                                                    orderBy, offset, limit, idx_names, kb_ids,
                                                    rank_feature=rank_feature)
                        total = self.dataStore.getTotal(res)
                    logging.debug("Dealer.search 2 TOTAL: {}".format(total))

            for k in keywords:
                kwds.add(k)
                for kk in rag_tokenizer.fine_grained_tokenize(k).split():
                    if len(kk) < 2:
                        continue
                    if kk in kwds:
                        continue
                    kwds.add(kk)

        logging.debug(f"TOTAL: {total}")
        ids = self.dataStore.getChunkIds(res)
        keywords = list(kwds)
        highlight = self.dataStore.getHighlight(res, keywords, "page_content")
        aggs = self.dataStore.getAggregation(res, "docnm_kwd")
        return self.SearchResult(
            total=total,
            ids=ids,
            query_vector=q_vec,
            aggregation=aggs,
            highlight=highlight,
            field=self.dataStore.getFields(res, src + ["_score"]),
            keywords=keywords
        )

    @staticmethod
    def trans2floats(txt):
        return [get_float(t) for t in txt.split("\t")]

    def insert_citations(self, answer, chunks, chunk_v,
                         embd_mdl, tkweight=0.1, vtweight=0.9):
        assert len(chunks) == len(chunk_v)
        if not chunks:
            return answer, set([])
        pieces = re.split(r"(```)", answer)
        if len(pieces) >= 3:
            i = 0
            pieces_ = []
            while i < len(pieces):
                if pieces[i] == "```":
                    st = i
                    i += 1
                    while i < len(pieces) and pieces[i] != "```":
                        i += 1
                    if i < len(pieces):
                        i += 1
                    pieces_.append("".join(pieces[st: i]) + "\n")
                else:
                    pieces_.extend(
                        re.split(
                            r"([^\|][；。？!！\n]|[a-z][.?;!][ \n])",
                            pieces[i]))
                    i += 1
            pieces = pieces_
        else:
            pieces = re.split(r"([^\|][；。？!！\n]|[a-z][.?;!][ \n])", answer)
        for i in range(1, len(pieces)):
            if re.match(r"([^\|][；。？!！\n]|[a-z][.?;!][ \n])", pieces[i]):
                pieces[i - 1] += pieces[i][0]
                pieces[i] = pieces[i][1:]
        idx = []
        pieces_ = []
        for i, t in enumerate(pieces):
            if len(t) < 5:
                continue
            idx.append(i)
            pieces_.append(t)
        logging.debug("{} => {}".format(answer, pieces_))
        if not pieces_:
            return answer, set([])

        ans_v, _ = embd_mdl.encode(pieces_)
        for i in range(len(chunk_v)):
            if len(ans_v[0]) != len(chunk_v[i]):
                chunk_v[i] = [0.0] * len(ans_v[0])
                logging.warning(
                    "The dimension of query and chunk do not match: {} vs. {}".format(len(ans_v[0]), len(chunk_v[i])))

        assert len(ans_v[0]) == len(chunk_v[0]), "The dimension of query and chunk do not match: {} vs. {}".format(
            len(ans_v[0]), len(chunk_v[0]))

        chunks_tks = [rag_tokenizer.tokenize(self.qryr.rmWWW(ck)).split()
                      for ck in chunks]
        cites = {}
        thr = 0.63
        while thr > 0.3 and len(cites.keys()) == 0 and pieces_ and chunks_tks:
            for i, a in enumerate(pieces_):
                sim, tksim, vtsim = self.qryr.hybrid_similarity(ans_v[i],
                                                                chunk_v,
                                                                rag_tokenizer.tokenize(
                                                                    self.qryr.rmWWW(pieces_[i])).split(),
                                                                chunks_tks,
                                                                tkweight, vtweight)
                mx = np.max(sim) * 0.99
                logging.debug("{} SIM: {}".format(pieces_[i], mx))
                if mx < thr:
                    continue
                cites[idx[i]] = list(
                    set([str(ii) for ii in range(len(chunk_v)) if sim[ii] > mx]))[:4]
            thr *= 0.8

        res = ""
        seted = set([])
        for i, p in enumerate(pieces):
            res += p
            if i not in idx:
                continue
            if i not in cites:
                continue
            for c in cites[i]:
                assert int(c) < len(chunk_v)
            for c in cites[i]:
                if c in seted:
                    continue
                res += f" [ID:{c}]"
                seted.add(c)

        return res, seted

    def _rank_feature_scores(self, query_rfea, search_res):
        ## For rank feature(tag_fea) scores.
        rank_fea = []
        pageranks = []
        for chunk_id in search_res.ids:
            pageranks.append(search_res.field[chunk_id].get(PAGERANK_FLD, 0))
        pageranks = np.array(pageranks, dtype=float)

        if not query_rfea:
            return np.array([0 for _ in range(len(search_res.ids))]) + pageranks

        q_denor = np.sqrt(np.sum([s * s for t, s in query_rfea.items() if t != PAGERANK_FLD]))
        for i in search_res.ids:
            nor, denor = 0, 0
            if not search_res.field[i].get(TAG_FLD):
                rank_fea.append(0)
                continue
            for t, sc in eval(search_res.field[i].get(TAG_FLD, "{}")).items():
                if t in query_rfea:
                    nor += query_rfea[t] * sc
                denor += sc * sc
            if denor == 0:
                rank_fea.append(0)
            else:
                rank_fea.append(nor / np.sqrt(denor) / q_denor)
        return np.array(rank_fea) * 10. + pageranks

    def rerank(self, sres, query, tkweight=0.3,
               vtweight=0.7, cfield="content_ltks",
               rank_feature: dict | None = None
               ):
        _, keywords = self.qryr.question(query)
        vector_size = len(sres.query_vector)
        vector_column = f"q_{vector_size}_vec"
        zero_vector = [0.0] * vector_size
        ins_embd = []
        for chunk_id in sres.ids:
            vector = sres.field[chunk_id].get(vector_column, zero_vector)
            if isinstance(vector, str):
                vector = [get_float(v) for v in vector.split("\t")]
            ins_embd.append(vector)
        if not ins_embd:
            return [], [], []

        for i in sres.ids:
            if isinstance(sres.field[i].get("important_kwd", []), str):
                sres.field[i]["important_kwd"] = [sres.field[i]["important_kwd"]]
        ins_tw = []
        for i in sres.ids:
            content_ltks = list(OrderedDict.fromkeys(sres.field[i][cfield].split()))
            title_tks = [t for t in sres.field[i].get("title_tks", "").split() if t]
            question_tks = [t for t in sres.field[i].get("question_tks", "").split() if t]
            important_kwd = sres.field[i].get("important_kwd", [])
            tks = content_ltks + title_tks * 2 + important_kwd * 5 + question_tks * 6
            ins_tw.append(tks)

        ## For rank feature(tag_fea) scores.
        rank_fea = self._rank_feature_scores(rank_feature, sres)

        sim, tksim, vtsim = self.qryr.hybrid_similarity(sres.query_vector,
                                                        ins_embd,
                                                        keywords,
                                                        ins_tw, tkweight, vtweight)

        return sim + rank_fea, tksim, vtsim

    def rerank_by_model(self, rerank_mdl, sres, query, tkweight=0.3,
                        vtweight=0.7, cfield="content_ltks",
                        rank_feature: dict | None = None):
        _, keywords = self.qryr.question(query)

        for i in sres.ids:
            if isinstance(sres.field[i].get("important_kwd", []), str):
                sres.field[i]["important_kwd"] = [sres.field[i]["important_kwd"]]
        ins_tw = []
        for i in sres.ids:
            content_ltks = sres.field[i][cfield].split()
            title_tks = [t for t in sres.field[i].get("title_tks", "").split() if t]
            important_kwd = sres.field[i].get("important_kwd", [])
            tks = content_ltks + title_tks + important_kwd
            ins_tw.append(tks)

        tksim = self.qryr.token_similarity(keywords, ins_tw)
        vtsim, _ = rerank_mdl.similarity(query, [remove_redundant_spaces(" ".join(tks)) for tks in ins_tw])
        ## For rank feature(tag_fea) scores.
        rank_fea = self._rank_feature_scores(rank_feature, sres)

        return tkweight * (np.array(tksim) + rank_fea) + vtweight * vtsim, tksim, vtsim

    def hybrid_similarity(self, ans_embd, ins_embd, ans, inst):
        return self.qryr.hybrid_similarity(ans_embd,
                                           ins_embd,
                                           rag_tokenizer.tokenize(ans).split(),
                                           rag_tokenizer.tokenize(inst).split())

    def retrieval(self, question, embd_mdl, workspace_ids, kb_ids, page, page_size, similarity_threshold=0.2,
                  vector_similarity_weight=0.3, top=1024, document_ids=None, aggs=True,
                  rerank_mdl=None, highlight=False,
                  rank_feature: dict | None = {PAGERANK_FLD: 10}):
        ranks = {"total": 0, "chunks": [], "doc_aggs": {}}
        if not question:
            return ranks

        # Ensure RERANK_LIMIT is multiple of page_size
        RERANK_LIMIT = math.ceil(64 / page_size) * page_size if page_size > 1 else 1
        req = {"kb_ids": kb_ids, "document_ids": document_ids, "page": math.ceil(page_size * page / RERANK_LIMIT),
               "size": RERANK_LIMIT,
               "question": question, "vector": True, "topk": top,
               "similarity": similarity_threshold,
               "available_int": 1}

        if isinstance(workspace_ids, str):
            workspace_ids = workspace_ids.split(",")

        sres = self.search(req, [index_name(workspace_id) for workspace_id in workspace_ids],
                           kb_ids, embd_mdl, highlight, rank_feature=rank_feature)

        if rerank_mdl and sres.total > 0:
            sim, tsim, vsim = self.rerank_by_model(rerank_mdl,
                                                   sres, question, 1 - vector_similarity_weight,
                                                   vector_similarity_weight,
                                                   rank_feature=rank_feature)
        else:
            # ElasticSearch doesn't normalize each way score before fusion.
            sim, tsim, vsim = self.rerank(
                sres, question, 1 - vector_similarity_weight, vector_similarity_weight,
                rank_feature=rank_feature)
        # Already paginated in search function
        max_pages = RERANK_LIMIT // page_size
        page_index = (page % max_pages) - 1
        begin = max(page_index * page_size, 0)
        sim = sim[begin: begin + page_size]
        sim_np = np.array(sim, dtype=np.float64)
        idx = np.argsort(sim_np * -1)
        dim = len(sres.query_vector)
        vector_column = f"q_{dim}_vec"
        zero_vector = [0.0] * dim
        filtered_count = (sim_np >= similarity_threshold).sum()
        ranks["total"] = int(filtered_count)  # Convert from np.int64 to Python int otherwise JSON serializable error
        for i in idx:
            if np.float64(sim[i]) < similarity_threshold:
                break

            id = sres.ids[i]
            chunk = sres.field[id]
            dnm = chunk.get("docnm_kwd", "")
            did = chunk.get("document_id", "")

            if len(ranks["chunks"]) >= page_size:
                if aggs:
                    if dnm not in ranks["doc_aggs"]:
                        ranks["doc_aggs"][dnm] = {"document_id": did, "count": 0}
                    ranks["doc_aggs"][dnm]["count"] += 1
                    continue
                break

            position_int = chunk.get("position_int", [])
            d = {
                "chunk_id": id,
                "content_ltks": chunk["content_ltks"],
                "page_content": chunk["page_content"],
                "document_id": did,
                "docnm_kwd": dnm,
                "kb_id": chunk["kb_id"],
                "important_kwd": chunk.get("important_kwd", []),
                "image_id": chunk.get("img_id", ""),
                "similarity": sim[i],
                "vector_similarity": vsim[i],
                "term_similarity": tsim[i],
                "vector": chunk.get(vector_column, zero_vector),
                "positions": position_int,
                "doc_type_kwd": chunk.get("doc_type_kwd", "")
            }
            if highlight and sres.highlight:
                if id in sres.highlight:
                    d["highlight"] = remove_redundant_spaces(sres.highlight[id])
                else:
                    d["highlight"] = d["page_content"]
            ranks["chunks"].append(d)
            if dnm not in ranks["doc_aggs"]:
                ranks["doc_aggs"][dnm] = {"document_id": did, "count": 0}
            ranks["doc_aggs"][dnm]["count"] += 1
        ranks["doc_aggs"] = [{"doc_name": k,
                              "document_id": v["document_id"],
                              "count": v["count"]} for k,
                             v in sorted(ranks["doc_aggs"].items(),
                                         key=lambda x: x[1]["count"] * -1)]
        ranks["chunks"] = ranks["chunks"][:page_size]

        return ranks

    def sql_retrieval(self, sql, fetch_size=128, format="json"):
        tbl = self.dataStore.sql(sql, fetch_size, format)
        return tbl

    def chunk_list(self, document_id: str, workspace_id: str,
                   kb_ids: list[str], max_count=1024,
                   offset=0,
                   fields=["docnm_kwd", "page_content", "img_id"],
                   sort_by_position: bool = False):
        condition = {"document_id": document_id}

        fields_set = set(fields or [])
        if sort_by_position:
            for need in ("page_num_int", "position_int", "top_int"):
                if need not in fields_set:
                    fields_set.add(need)
        fields = list(fields_set)

        orderBy = OrderByExpr()
        if sort_by_position:
            orderBy.asc("page_num_int")
            orderBy.asc("position_int")
            orderBy.asc("top_int")

        res = []
        bs = 128
        for p in range(offset, max_count, bs):
            es_res = self.dataStore.search(fields, [], condition, [], orderBy, p, bs, index_name(workspace_id),
                                           kb_ids)
            dict_chunks = self.dataStore.getFields(es_res, fields)
            for id, doc in dict_chunks.items():
                doc["id"] = id
            if dict_chunks:
                res.extend(dict_chunks.values())
            if len(dict_chunks.values()) < bs:
                break
        return res

    def all_tags(self, workspace_id: str, kb_ids: list[str], S=1000):
        if not self.dataStore.indexExist(index_name(workspace_id), kb_ids[0]):
            return []
        res = self.dataStore.search([], [], {}, [], OrderByExpr(), 0, 0, index_name(workspace_id), kb_ids, ["tag_kwd"])
        return self.dataStore.getAggregation(res, "tag_kwd")

    def all_tags_in_portion(self, workspace_id: str, kb_ids: list[str], S=1000):
        res = self.dataStore.search([], [], {}, [], OrderByExpr(), 0, 0, index_name(workspace_id), kb_ids, ["tag_kwd"])
        res = self.dataStore.getAggregation(res, "tag_kwd")
        total = np.sum([c for _, c in res])
        return {t: (c + 1) / (total + S) for t, c in res}

    def tag_content(self, workspace_id: str, kb_ids: list[str], doc, all_tags, topn_tags=3, keywords_topn=30, S=1000):
        idx_nm = index_name(workspace_id)
        match_txt = self.qryr.paragraph(doc["title_tks"] + " " + doc["content_ltks"], doc.get("important_kwd", []),
                                        keywords_topn)
        res = self.dataStore.search([], [], {}, [match_txt], OrderByExpr(), 0, 0, idx_nm, kb_ids, ["tag_kwd"])
        aggs = self.dataStore.getAggregation(res, "tag_kwd")
        if not aggs:
            return False
        cnt = np.sum([c for _, c in aggs])
        tag_fea = sorted([(a, round(0.1 * (c + 1) / (cnt + S) / max(1e-6, all_tags.get(a, 0.0001)))) for a, c in aggs],
                         key=lambda x: x[1] * -1)[:topn_tags]
        doc[TAG_FLD] = {a.replace(".", "_"): c for a, c in tag_fea if c > 0}
        return True

    def tag_query(self, question: str, workspace_ids: str | list[str], kb_ids: list[str], all_tags, topn_tags=3, S=1000):
        if isinstance(workspace_ids, str):
            idx_nms = index_name(workspace_ids)
        else:
            idx_nms = [index_name(workspace_id) for workspace_id in workspace_ids]
        match_txt, _ = self.qryr.question(question, min_match=0.0)
        res = self.dataStore.search([], [], {}, [match_txt], OrderByExpr(), 0, 0, idx_nms, kb_ids, ["tag_kwd"])
        aggs = self.dataStore.getAggregation(res, "tag_kwd")
        if not aggs:
            return {}
        cnt = np.sum([c for _, c in aggs])
        tag_fea = sorted([(a, round(0.1 * (c + 1) / (cnt + S) / max(1e-6, all_tags.get(a, 0.0001)))) for a, c in aggs],
                         key=lambda x: x[1] * -1)[:topn_tags]
        return {a.replace(".", "_"): max(1, c) for a, c in tag_fea}

    def retrieval_by_toc(self, query: str, chunks: list[dict], workspace_ids: list[str], chat_mdl, topn: int = 6):
        if not chunks:
            return []
        idx_nms = [index_name(workspace_id) for workspace_id in workspace_ids]
        ranks, document_id2kb_id = {}, {}
        for ck in chunks:
            if ck["document_id"] not in ranks:
                ranks[ck["document_id"]] = 0
            ranks[ck["document_id"]] += ck["similarity"]
            document_id2kb_id[ck["document_id"]] = ck["kb_id"]
        document_id = sorted(ranks.items(), key=lambda x: x[1] * -1.)[0][0]
        kb_ids = [document_id2kb_id[document_id]]
        es_res = self.dataStore.search(["page_content"], [], {"document_id": document_id, "toc_kwd": "toc"}, [],
                                       OrderByExpr(), 0, 128, idx_nms,
                                       kb_ids)
        toc = []
        dict_chunks = self.dataStore.getFields(es_res, ["page_content"])
        for _, doc in dict_chunks.items():
            try:
                toc.extend(json.loads(doc["page_content"]))
            except Exception as e:
                logging.exception(e)
        if not toc:
            return chunks

        ids = relevant_chunks_with_toc(query, toc, chat_mdl, topn * 2)
        if not ids:
            return chunks

        vector_size = 1024
        id2idx = {ck["chunk_id"]: i for i, ck in enumerate(chunks)}
        for cid, sim in ids:
            if cid in id2idx:
                chunks[id2idx[cid]]["similarity"] += sim
                continue
            chunk = self.dataStore.get(cid, idx_nms, kb_ids)
            d = {
                "chunk_id": cid,
                "content_ltks": chunk["content_ltks"],
                "page_content": chunk["page_content"],
                "document_id": document_id,
                "docnm_kwd": chunk.get("docnm_kwd", ""),
                "kb_id": chunk["kb_id"],
                "important_kwd": chunk.get("important_kwd", []),
                "image_id": chunk.get("img_id", ""),
                "similarity": sim,
                "vector_similarity": sim,
                "term_similarity": sim,
                "vector": [0.0] * vector_size,
                "positions": chunk.get("position_int", []),
                "doc_type_kwd": chunk.get("doc_type_kwd", "")
            }
            for k in chunk.keys():
                if k[-4:] == "_vec":
                    d["vector"] = chunk[k]
                    vector_size = len(chunk[k])
                    break
            chunks.append(d)

        return sorted(chunks, key=lambda x: x["similarity"] * -1)[:topn]