import json
import logging
import os

import networkx as nx
import trio

from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVector
from app.core.rag.common.exceptions import TaskCanceledException
from app.core.rag.common.misc_utils import get_uuid
from app.core.rag.common.connection_utils import timeout
from app.core.rag.graphrag.entity_resolution import EntityResolution
from app.core.rag.graphrag.general.community_reports_extractor import CommunityReportsExtractor
from app.core.rag.graphrag.general.extractor import Extractor
from app.core.rag.graphrag.general.graph_extractor import GraphExtractor as GeneralKGExt
from app.core.rag.graphrag.light.graph_extractor import GraphExtractor as LightKGExt
from app.core.rag.graphrag.utils import (
    GraphChange,
    chunk_id,
    does_graph_contains,
    get_graph,
    graph_merge,
    set_graph,
    tidy_graph,
    has_canceled,
)
from app.core.rag.nlp import rag_tokenizer, search
from app.core.rag.utils.redis_conn import RedisDistributedLock
from app.core.rag.common import settings


def init_graphrag(row, vector_size: int):
    idxnm = search.index_name(row["workspace_id"])
    return settings.docStoreConn.createIdx(idxnm, row.get("kb_id", ""), vector_size)

async def run_graphrag(
    row: dict,
    language,
    with_resolution: bool,
    with_community: bool,
    chat_model,
    embedding_model,
    callback,
):
    enable_timeout_assertion = os.environ.get("ENABLE_TIMEOUT_ASSERTION")
    start = trio.current_time()
    workspace_id, kb_id, document_id = row["workspace_id"], str(row["kb_id"]), row["document_id"]
    chunks = []
    for d in settings.retriever.chunk_list(document_id, workspace_id, [kb_id], fields=["page_content", "document_id"], sort_by_position=True):
        chunks.append(d["page_content"])

    with trio.fail_after(max(120, len(chunks) * 60 * 10) if enable_timeout_assertion else 10000000000):
        subgraph = await generate_subgraph(
            LightKGExt if "method" not in row["parser_config"].get("graphrag", {}) or row["parser_config"]["graphrag"]["method"] != "general" else GeneralKGExt,
            workspace_id,
            kb_id,
            document_id,
            chunks,
            language,
            row["parser_config"]["graphrag"].get("entity_types", []),
            chat_model,
            embedding_model,
            callback,
        )

    if not subgraph:
        return

    graphrag_task_lock = RedisDistributedLock(f"graphrag_task_{kb_id}", lock_value=document_id, timeout=1200)
    await graphrag_task_lock.spin_acquire()
    callback(msg=f"run_graphrag {document_id} graphrag_task_lock acquired")

    try:
        subgraph_nodes = set(subgraph.nodes())
        new_graph = await merge_subgraph(
            workspace_id,
            kb_id,
            document_id,
            subgraph,
            embedding_model,
            callback,
        )
        assert new_graph is not None

        if not with_resolution and not with_community:
            return

        if with_resolution:
            await graphrag_task_lock.spin_acquire()
            callback(msg=f"run_graphrag {document_id} graphrag_task_lock acquired")
            await resolve_entities(
                new_graph,
                subgraph_nodes,
                workspace_id,
                kb_id,
                document_id,
                chat_model,
                embedding_model,
                callback,
                task_id=row["id"],
            )
        if with_community:
            await graphrag_task_lock.spin_acquire()
            callback(msg=f"run_graphrag {document_id} graphrag_task_lock acquired")
            await extract_community(
                new_graph,
                workspace_id,
                kb_id,
                document_id,
                chat_model,
                embedding_model,
                callback,
                task_id=row["id"],
            )
    finally:
        graphrag_task_lock.release()
    now = trio.current_time()
    callback(msg=f"GraphRAG for doc {document_id} done in {now - start:.2f} seconds.")
    return


async def run_graphrag_for_kb(
    row: dict,
    document_ids: list[str],
    language: str,
    parser_config: dict,
    vector_service: ElasticSearchVector,
    chat_model,
    embedding_model,
    callback,
    *,
    with_resolution: bool = True,
    with_community: bool = True,
    max_parallel_documents: int = 4,
) -> dict:
    workspace_id, kb_id = row["workspace_id"], row["kb_id"]
    enable_timeout_assertion = os.environ.get("ENABLE_TIMEOUT_ASSERTION")
    start = trio.current_time()

    document_ids = list(dict.fromkeys(document_ids)) # Remove duplicate elements
    if not document_ids:
        callback(msg=f"[GraphRAG] kb:{kb_id} has no processable document_id.")
        return {"ok_documents": [], "failed_documents": [], "total_documents": 0, "total_chunks": 0, "seconds": 0.0}

    def load_doc_chunks(document_id: str) -> list[str]:
        from app.core.rag.common.token_utils import num_tokens_from_string

        chunks = []
        current_chunk = ""

        total, items = vector_service.search_by_segment(document_id=str(document_id), query=None, pagesize=9999, page=1, asc=True)
        for doc in items:
            content = doc.page_content
            if num_tokens_from_string(current_chunk + content) < 1024:
                current_chunk += content
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = content

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    all_document_chunks: dict[str, list[str]] = {}
    total_chunks = 0
    for document_id in document_ids:
        chunks = load_doc_chunks(document_id)
        all_document_chunks[document_id] = chunks
        total_chunks += len(chunks)

    if total_chunks == 0:
        callback(msg=f"[GraphRAG] kb:{kb_id} has no available chunks in all documents, skip.")
        return {"ok_documents": [], "failed_documents": document_ids, "total_documents": len(document_ids), "total_chunks": 0, "seconds": 0.0}

    semaphore = trio.Semaphore(max_parallel_documents)

    subgraphs: dict[str, object] = {}
    failed_documents: list[tuple[str, str]] = []  # (document_id, error)

    async def build_one(document_id: str):
        if has_canceled(row["id"]):
            callback(msg=f"Task {row['id']} cancelled, stopping execution.")
            raise TaskCanceledException(f"Task {row['id']} was cancelled")

        chunks = all_document_chunks.get(document_id, [])
        if not chunks:
            callback(msg=f"[GraphRAG] doc:{document_id} has no available chunks, skip generation.")
            return

        kg_extractor = LightKGExt if ("method" not in parser_config.get("graphrag", {}) or parser_config["graphrag"]["method"] != "general") else GeneralKGExt

        deadline = max(120, len(chunks) * 60 * 10) if enable_timeout_assertion else 10000000000

        async with semaphore:
            try:
                msg = f"[GraphRAG] build_subgraph document:{document_id}"
                callback(msg=f"{msg} start (chunks={len(chunks)}, timeout={deadline}s)")
                with trio.fail_after(deadline):
                    sg = await generate_subgraph(
                        kg_extractor,
                        workspace_id,
                        kb_id,
                        document_id,
                        chunks,
                        language,
                        parser_config.get("graphrag", {}).get("entity_types", []),
                        chat_model,
                        embedding_model,
                        callback,
                        task_id=row["id"]
                    )
                if sg:
                    subgraphs[document_id] = sg
                    callback(msg=f"{msg} done")
                else:
                    failed_documents.append((document_id, "subgraph is empty"))
                    callback(msg=f"{msg} empty")
            except TaskCanceledException as canceled:
                callback(msg=f"[GraphRAG] build_subgraph document:{document_id} FAILED: {canceled}")
            except Exception as e:
                failed_documents.append((document_id, repr(e)))
                callback(msg=f"[GraphRAG] build_subgraph document:{document_id} FAILED: {e!r}")

    if has_canceled(row["id"]):
        callback(msg=f"Task {row['id']} cancelled before processing documents.")
        raise TaskCanceledException(f"Task {row['id']} was cancelled")

    async with trio.open_nursery() as nursery:
        for document_id in document_ids:
            nursery.start_soon(build_one, document_id)

    if has_canceled(row["id"]):
        callback(msg=f"Task {row['id']} cancelled after document processing.")
        raise TaskCanceledException(f"Task {row['id']} was cancelled")

    ok_documents = [d for d in document_ids if d in subgraphs]
    if not ok_documents:
        callback(msg=f"[GraphRAG] kb:{kb_id} no subgraphs generated successfully, end.")
        now = trio.current_time()
        return {"ok_documents": [], "failed_documents": failed_documents, "total_documents": len(document_ids), "total_chunks": total_chunks, "seconds": now - start}

    kb_lock = RedisDistributedLock(f"graphrag_task_{kb_id}", lock_value="batch_merge", timeout=1200)
    await kb_lock.spin_acquire()
    callback(msg=f"[GraphRAG] kb:{kb_id} merge lock acquired")

    if has_canceled(row["id"]):
        callback(msg=f"Task {row['id']} cancelled before merging subgraphs.")
        raise TaskCanceledException(f"Task {row['id']} was cancelled")

    try:
        union_nodes: set = set()
        final_graph = None

        for document_id in ok_documents:
            sg = subgraphs[document_id]
            union_nodes.update(set(sg.nodes()))

            new_graph = await merge_subgraph(
                workspace_id,
                kb_id,
                document_id,
                sg,
                embedding_model,
                callback,
            )
            if new_graph is not None:
                final_graph = new_graph

        if final_graph is None:
            callback(msg=f"[GraphRAG] kb:{kb_id} merge finished (no in-memory graph returned).")
        else:
            callback(msg=f"[GraphRAG] kb:{kb_id} merge finished, graph ready.")
    finally:
        kb_lock.release()

    if not with_resolution and not with_community:
        now = trio.current_time()
        callback(msg=f"[GraphRAG] KB merge done in {now - start:.2f}s. ok={len(ok_documents)} / total={len(document_ids)}")
        return {"ok_documents": ok_documents, "failed_documents": failed_documents, "total_documents": len(document_ids), "total_chunks": total_chunks, "seconds": now - start}

    if has_canceled(row["id"]):
        callback(msg=f"Task {row['id']} cancelled before resolution/community extraction.")
        raise TaskCanceledException(f"Task {row['id']} was cancelled")

    await kb_lock.spin_acquire()
    callback(msg=f"[GraphRAG] kb:{kb_id} post-merge lock acquired for resolution/community")

    try:
        subgraph_nodes = set()
        for sg in subgraphs.values():
            subgraph_nodes.update(set(sg.nodes()))

        if with_resolution:
            await resolve_entities(
                final_graph,
                subgraph_nodes,
                workspace_id,
                kb_id,
                None,
                chat_model,
                embedding_model,
                callback,
                task_id=row["id"],
            )

        if with_community:
            await extract_community(
                final_graph,
                workspace_id,
                kb_id,
                None,
                chat_model,
                embedding_model,
                callback,
                task_id=row["id"],
            )
    finally:
        kb_lock.release()

    now = trio.current_time()
    callback(msg=f"[GraphRAG] GraphRAG for KB {kb_id} done in {now - start:.2f} seconds. ok={len(ok_documents)} failed={len(failed_documents)} total_documents={len(document_ids)} total_chunks={total_chunks}")
    return {
        "ok_documents": ok_documents,
        "failed_documents": failed_documents,  # [(document_id, error), ...]
        "total_documents": len(document_ids),
        "total_chunks": total_chunks,
        "seconds": now - start,
    }


async def generate_subgraph(
    extractor: Extractor,
    workspace_id: str,
    kb_id: str,
    document_id: str,
    chunks: list[str],
    language,
    entity_types,
    llm_bdl,
    embed_bdl,
    callback,
    task_id: str = "",
):
    if task_id and has_canceled(task_id):
        callback(msg=f"Task {task_id} cancelled during subgraph generation for document {document_id}.")
        raise TaskCanceledException(f"Task {task_id} was cancelled")

    contains = await does_graph_contains(workspace_id, kb_id, document_id)
    if contains:
        callback(msg=f"Graph already contains {document_id}")
        return None
    start = trio.current_time()
    ext = extractor(
        llm_bdl,
        language=language,
        entity_types=entity_types,
    )
    ents, rels = await ext(document_id, chunks, callback, task_id=task_id)
    subgraph = nx.Graph()

    for ent in ents:
        if task_id and has_canceled(task_id):
            callback(msg=f"Task {task_id} cancelled during entity processing for document {document_id}.")
            raise TaskCanceledException(f"Task {task_id} was cancelled")

        assert "description" in ent, f"entity {ent} does not have description"
        ent["source_id"] = [document_id]
        subgraph.add_node(ent["entity_name"], **ent)

    ignored_rels = 0
    for rel in rels:
        if task_id and has_canceled(task_id):
            callback(msg=f"Task {task_id} cancelled during relationship processing for document {document_id}.")
            raise TaskCanceledException(f"Task {task_id} was cancelled")

        assert "description" in rel, f"relation {rel} does not have description"
        if not subgraph.has_node(rel["src_id"]) or not subgraph.has_node(rel["tgt_id"]):
            ignored_rels += 1
            continue
        rel["source_id"] = [document_id]
        subgraph.add_edge(
            rel["src_id"],
            rel["tgt_id"],
            **rel,
        )
    if ignored_rels:
        callback(msg=f"ignored {ignored_rels} relations due to missing entities.")
    tidy_graph(subgraph, callback, check_attribute=False)

    subgraph.graph["source_id"] = [document_id]
    chunk = {
        "page_content": json.dumps(nx.node_link_data(subgraph, edges="edges"), ensure_ascii=False),
        "knowledge_graph_kwd": "subgraph",
        "kb_id": kb_id,
        "source_id": [document_id],
        "available_int": 0,
        "removed_kwd": "N",
    }
    cid = chunk_id(chunk)
    await trio.to_thread.run_sync(settings.docStoreConn.delete, {"knowledge_graph_kwd": "subgraph", "source_id": document_id}, search.index_name(workspace_id), kb_id)
    await trio.to_thread.run_sync(settings.docStoreConn.insert, [{"id": cid, **chunk}], search.index_name(workspace_id), kb_id)
    now = trio.current_time()
    callback(msg=f"generated subgraph for document {document_id} in {now - start:.2f} seconds.")
    return subgraph


@timeout(60 * 3)
async def merge_subgraph(
    workspace_id: str,
    kb_id: str,
    document_id: str,
    subgraph: nx.Graph,
    embedding_model,
    callback,
):
    start = trio.current_time()
    change = GraphChange()
    old_graph = await get_graph(workspace_id, kb_id, subgraph.graph["source_id"])
    if old_graph is not None:
        logging.info("Merge with an exiting graph...................")
        tidy_graph(old_graph, callback)
        new_graph = graph_merge(old_graph, subgraph, change)
    else:
        new_graph = subgraph
        change.added_updated_nodes = set(new_graph.nodes())
        change.added_updated_edges = set(new_graph.edges())
    pr = nx.pagerank(new_graph)
    for node_name, pagerank in pr.items():
        new_graph.nodes[node_name]["pagerank"] = pagerank

    await set_graph(workspace_id, kb_id, embedding_model, new_graph, change, callback)
    now = trio.current_time()
    callback(msg=f"merging subgraph for document {document_id} into the global graph done in {now - start:.2f} seconds.")
    return new_graph


@timeout(60 * 30, 1)
async def resolve_entities(
    graph,
    subgraph_nodes: set[str],
    workspace_id: str,
    kb_id: str,
    document_id: str,
    llm_bdl,
    embed_bdl,
    callback,
    task_id: str = "",
):
    # Check if task has been canceled before resolution
    if task_id and has_canceled(task_id):
        callback(msg=f"Task {task_id} cancelled during entity resolution.")
        raise TaskCanceledException(f"Task {task_id} was cancelled")

    start = trio.current_time()
    er = EntityResolution(
        llm_bdl,
    )
    reso = await er(graph, subgraph_nodes, callback=callback, task_id=task_id)
    graph = reso.graph
    change = reso.change
    callback(msg=f"Graph resolution removed {len(change.removed_nodes)} nodes and {len(change.removed_edges)} edges.")
    callback(msg="Graph resolution updated pagerank.")

    if task_id and has_canceled(task_id):
        callback(msg=f"Task {task_id} cancelled after entity resolution.")
        raise TaskCanceledException(f"Task {task_id} was cancelled")

    await set_graph(workspace_id, kb_id, embed_bdl, graph, change, callback)
    now = trio.current_time()
    callback(msg=f"Graph resolution done in {now - start:.2f}s.")


@timeout(60 * 30, 1)
async def extract_community(
    graph,
    workspace_id: str,
    kb_id: str,
    document_id: str,
    llm_bdl,
    embed_bdl,
    callback,
    task_id: str = "",
):
    if task_id and has_canceled(task_id):
        callback(msg=f"Task {task_id} cancelled before community extraction.")
        raise TaskCanceledException(f"Task {task_id} was cancelled")

    start = trio.current_time()
    ext = CommunityReportsExtractor(
        llm_bdl,
    )
    cr = await ext(graph, callback=callback, task_id=task_id)

    if task_id and has_canceled(task_id):
        callback(msg=f"Task {task_id} cancelled during community extraction.")
        raise TaskCanceledException(f"Task {task_id} was cancelled")

    community_structure = cr.structured_output
    community_reports = cr.output
    document_ids = graph.graph["source_id"]

    now = trio.current_time()
    callback(msg=f"Graph extracted {len(cr.structured_output)} communities in {now - start:.2f}s.")
    start = now
    if task_id and has_canceled(task_id):
        callback(msg=f"Task {task_id} cancelled during community indexing.")
        raise TaskCanceledException(f"Task {task_id} was cancelled")

    chunks = []
    for stru, rep in zip(community_structure, community_reports):
        obj = {
            "report": rep,
            "evidences": "\n".join([f.get("explanation", "") for f in stru["findings"]]),
        }
        chunk = {
            "id": get_uuid(),
            "docnm_kwd": stru["title"],
            "title_tks": rag_tokenizer.tokenize(stru["title"]),
            "page_content": json.dumps(obj, ensure_ascii=False),
            "content_ltks": rag_tokenizer.tokenize(obj["report"] + " " + obj["evidences"]),
            "knowledge_graph_kwd": "community_report",
            "weight_flt": stru["weight"],
            "entities_kwd": stru["entities"],
            "important_kwd": stru["entities"],
            "kb_id": kb_id,
            "source_id": list(document_ids),
            "available_int": 0,
        }
        chunk["content_sm_ltks"] = rag_tokenizer.fine_grained_tokenize(chunk["content_ltks"])
        chunks.append(chunk)

    await trio.to_thread.run_sync(
        lambda: settings.docStoreConn.delete(
            {"knowledge_graph_kwd": "community_report", "kb_id": kb_id},
            search.index_name(workspace_id),
            kb_id,
        )
    )
    es_bulk_size = 4
    for b in range(0, len(chunks), es_bulk_size):
        document_store_result = await trio.to_thread.run_sync(lambda: settings.docStoreConn.insert(chunks[b : b + es_bulk_size], search.index_name(workspace_id), kb_id))
        if document_store_result:
            error_message = f"Insert chunk error: {document_store_result}, please check log file and Elasticsearch status!"
            raise Exception(error_message)

    if task_id and has_canceled(task_id):
        callback(msg=f"Task {task_id} cancelled after community indexing.")
        raise TaskCanceledException(f"Task {task_id} was cancelled")

    now = trio.current_time()
    callback(msg=f"Graph indexed {len(cr.structured_output)} communities in {now - start:.2f}s.")
    return community_structure, community_reports
