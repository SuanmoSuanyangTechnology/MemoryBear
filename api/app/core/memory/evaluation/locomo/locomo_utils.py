"""
LoCoMo Utilities Module

This module provides helper functions for the LoCoMo benchmark evaluation:
- Data loading from JSON files
- Conversation extraction for ingestion
- Temporal reference resolution
- Context selection and formatting
- Retrieval wrapper functions
- Ingestion wrapper functions
"""

import os
import json
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from app.core.memory.utils.definitions import PROJECT_ROOT
from app.core.memory.evaluation.extraction_utils import ingest_contexts_via_full_pipeline


def load_locomo_data(
    data_path: str, 
    sample_size: int,
    conversation_index: int = 0
) -> List[Dict[str, Any]]:
    """
    Load LoCoMo dataset from JSON file.
    
    The LoCoMo dataset structure is a list of conversation objects, where each
    object contains a "qa" list of question-answer pairs.
    
    Args:
        data_path: Path to locomo10.json file
        sample_size: Number of QA pairs to load (limits total QA items returned)
        conversation_index: Which conversation to load QA pairs from (default: 0 for first)
        
    Returns:
        List of QA item dictionaries, each containing:
            - question: str
            - answer: str
            - category: int (1-4)
            - evidence: List[str]
            
    Raises:
        FileNotFoundError: If data_path does not exist
        json.JSONDecodeError: If file is not valid JSON
        IndexError: If conversation_index is out of range
    """
    if not os.path.exists(data_path):
        raise FileNotFoundError(f"LoCoMo data file not found: {data_path}")
    
    with open(data_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    
    # LoCoMo data structure: list of objects, each with a "qa" list
    qa_items: List[Dict[str, Any]] = []
    
    if isinstance(raw, list):
        # Only load QA pairs from the specified conversation
        if conversation_index < len(raw):
            entry = raw[conversation_index]
            if isinstance(entry, dict) and "qa" in entry:
                qa_items.extend(entry.get("qa", []))
        else:
            raise IndexError(
                f"Conversation index {conversation_index} out of range. "
                f"Dataset has {len(raw)} conversations."
            )
    else:
        # Fallback: single object with qa list
        if conversation_index == 0:
            qa_items.extend(raw.get("qa", []))
        else:
            raise IndexError(
                f"Conversation index {conversation_index} out of range. "
                f"Dataset has only 1 conversation."
            )
    
    # Return only the requested sample size
    return qa_items[:sample_size]


def extract_conversations(data_path: str, max_dialogues: int = 1) -> List[str]:
    """
    Extract conversation texts from LoCoMo data for ingestion.
    
    This function extracts the raw conversation dialogues from the LoCoMo dataset
    so they can be ingested into the memory system. Each conversation is formatted
    as a multi-line string with "role: message" format.
    
    Args:
        data_path: Path to locomo10.json file
        max_dialogues: Maximum number of dialogues to extract (default: 1)
        
    Returns:
        List of conversation strings formatted for ingestion.
        Each string contains multiple lines in format "role: message"
        
    Example output:
        [
            "User: I went to the store yesterday.\\nAI: What did you buy?\\n...",
            "User: I love hiking.\\nAI: Where do you like to hike?\\n..."
        ]
    """
    if not os.path.exists(data_path):
        raise FileNotFoundError(f"LoCoMo data file not found: {data_path}")
    
    with open(data_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    
    # Ensure we have a list of entries
    entries = raw if isinstance(raw, list) else [raw]
    
    contents: List[str] = []
    
    for i, entry in enumerate(entries[:max_dialogues]):
        if not isinstance(entry, dict):
            continue
        
        conv = entry.get("conversation", {})
        
        if not isinstance(conv, dict):
            continue
        
        lines: List[str] = []
        
        # Collect all session_* messages
        for key, val in sorted(conv.items()):
            if isinstance(val, list) and key.startswith("session_"):
                for msg in val:
                    if not isinstance(msg, dict):
                        continue
                    
                    role = msg.get("speaker") or "User"
                    text = msg.get("text") or ""
                    text = str(text).strip()
                    
                    if not text:
                        continue
                    
                    lines.append(f"{role}: {text}")
        
        if lines:
            contents.append("\n".join(lines))
    
    return contents


def resolve_temporal_references(text: str, anchor_date: datetime) -> str:
    """
    Resolve relative temporal references to absolute dates.
    
    This function converts relative time expressions (like "today", "yesterday",
    "3 days ago") into absolute ISO date strings based on an anchor date.
    
    Supported patterns:
    - today, yesterday, tomorrow
    - X days ago, in X days
    - last week, next week
    
    Args:
        text: Text containing temporal references
        anchor_date: Reference date for resolution (datetime object)
        
    Returns:
        Text with temporal references replaced by ISO dates (YYYY-MM-DD format)
        
    Example:
        >>> anchor = datetime(2023, 5, 8)
        >>> resolve_temporal_references("I saw him yesterday", anchor)
        "I saw him 2023-05-07"
    """
    # Ensure input is a string
    t = str(text) if text is not None else ""
    
    # today / yesterday / tomorrow
    t = re.sub(
        r"\btoday\b",
        anchor_date.date().isoformat(),
        t,
        flags=re.IGNORECASE
    )
    t = re.sub(
        r"\byesterday\b",
        (anchor_date - timedelta(days=1)).date().isoformat(),
        t,
        flags=re.IGNORECASE
    )
    t = re.sub(
        r"\btomorrow\b",
        (anchor_date + timedelta(days=1)).date().isoformat(),
        t,
        flags=re.IGNORECASE
    )
    
    # X days ago
    def _ago_repl(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return (anchor_date - timedelta(days=n)).date().isoformat()
    
    # in X days
    def _in_repl(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return (anchor_date + timedelta(days=n)).date().isoformat()
    
    t = re.sub(
        r"\b(\d+)\s+days?\s+ago\b",
        _ago_repl,
        t,
        flags=re.IGNORECASE
    )
    t = re.sub(
        r"\bin\s+(\d+)\s+days?\b",
        _in_repl,
        t,
        flags=re.IGNORECASE
    )
    
    # last week / next week (approximate as 7 days)
    t = re.sub(
        r"\blast\s+week\b",
        (anchor_date - timedelta(days=7)).date().isoformat(),
        t,
        flags=re.IGNORECASE
    )
    t = re.sub(
        r"\bnext\s+week\b",
        (anchor_date + timedelta(days=7)).date().isoformat(),
        t,
        flags=re.IGNORECASE
    )
    
    return t


def select_and_format_information(
    retrieved_info: List[str],
    question: str,
    max_chars: int = 8000
) -> str:
    """
    Intelligently select and format most relevant retrieved information for LLM prompt.
    
    This function scores each piece of retrieved information based on keyword matching
    with the question, then selects the highest-scoring pieces up to the character limit.
    
    Scoring criteria:
    - Keyword matches (higher weight for multiple occurrences)
    - Context length (moderate length preferred)
    - Position (earlier contexts get bonus points)
    
    Args:
        retrieved_info: List of retrieved information strings (chunks, statements, entities)
        question: Question being answered
        max_chars: Maximum total characters to include in final prompt
        
    Returns:
        Formatted string combining the most relevant information for LLM prompt.
        Contexts are separated by double newlines.
        
    Example:
        >>> contexts = ["Alice went to Paris", "Bob likes pizza", "Alice visited the Eiffel Tower"]
        >>> question = "Where did Alice go?"
        >>> select_and_format_information(contexts, question, max_chars=100)
        "Alice went to Paris\\n\\nAlice visited the Eiffel Tower"
    """
    if not retrieved_info:
        return ""
    
    # Extract question keywords (filter out stop words and short words)
    question_lower = question.lower()
    stop_words = {
        'what', 'when', 'where', 'who', 'why', 'how',
        'did', 'do', 'does', 'is', 'are', 'was', 'were',
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at'
    }
    question_words = set(re.findall(r'\b\w+\b', question_lower))
    question_words = {
        word for word in question_words
        if word not in stop_words and len(word) > 2
    }
    
    # Score each context
    scored_contexts = []
    for i, context in enumerate(retrieved_info):
        context_lower = context.lower()
        score = 0
        
        # Keyword matching score
        keyword_matches = 0
        for word in question_words:
            if word in context_lower:
                keyword_matches += 1
                # Multiple occurrences increase score
                score += context_lower.count(word) * 2
        
        # Length score (prefer moderate length)
        context_len = len(context)
        if 100 < context_len < 2000:
            score += 5
        elif context_len >= 2000:
            score += 2
        
        # Position bonus (earlier contexts often more relevant)
        if i < 3:
            score += 3
        
        scored_contexts.append((score, context, keyword_matches))
    
    # Sort by score (descending)
    scored_contexts.sort(key=lambda x: x[0], reverse=True)
    
    # Select contexts up to character limit
    selected = []
    total_chars = 0
    
    for score, context, matches in scored_contexts:
        if total_chars + len(context) <= max_chars:
            selected.append(context)
            total_chars += len(context)
        else:
            # Try to include high-scoring context by truncating
            if score > 10 and total_chars < max_chars - 500:
                remaining = max_chars - total_chars
                # Find lines with keywords
                lines = context.split('\n')
                relevant_lines = []
                current_chars = 0
                
                for line in lines:
                    line_lower = line.lower()
                    line_relevance = any(word in line_lower for word in question_words)
                    
                    if line_relevance and current_chars < remaining - 100:
                        relevant_lines.append(line)
                        current_chars += len(line)
                
                if relevant_lines and len('\n'.join(relevant_lines)) > 100:
                    truncated = '\n'.join(relevant_lines)
                    selected.append(truncated + "\n[Content truncated...]")
                    total_chars += len(truncated)
            break
    
    return "\n\n".join(selected)


async def retrieve_relevant_information(
    question: str,
    group_id: str,
    search_type: str,
    search_limit: int,
    connector: Any,
    embedder: Any
) -> List[str]:
    """
    Retrieve relevant information from memory graph for a question.
    
    This function searches the Neo4j memory graph (populated during ingestion) and
    returns relevant chunks, statements, and entity information that might help
    answer the question.
    
    The function supports three search types:
    - "keyword": Full-text search using Cypher queries
    - "embedding": Vector similarity search using embeddings
    - "hybrid": Combination of keyword and embedding search with reranking
    
    Args:
        question: Question to search for
        group_id: Database group ID (identifies which conversation memory to search)
        search_type: "keyword", "embedding", or "hybrid"
        search_limit: Max memory pieces to retrieve
        connector: Neo4j connector instance
        embedder: Embedder client instance
        
    Returns:
        List of text strings (chunks, statements, entity summaries) from memory graph.
        Each string represents a piece of retrieved information.
        
    Raises:
        Exception: If search fails (caught and returns empty list)
    """
    from app.repositories.neo4j.graph_search import (
        search_graph,
        search_graph_by_embedding
    )
    from app.core.memory.storage_services.search import run_hybrid_search
    
    contexts_all: List[str] = []
    
    try:
        if search_type == "embedding":
            # Embedding-based search
            search_results = await search_graph_by_embedding(
                connector=connector,
                embedder_client=embedder,
                query_text=question,
                group_id=group_id,
                limit=search_limit,
                include=["chunks", "statements", "entities", "summaries"],
            )
            
            chunks = search_results.get("chunks", [])
            statements = search_results.get("statements", [])
            entities = search_results.get("entities", [])
            summaries = search_results.get("summaries", [])
            
            # Build context from chunks
            for c in chunks:
                content = str(c.get("content", "")).strip()
                if content:
                    contexts_all.append(content)
            
            # Add statements
            for s in statements:
                stmt_text = str(s.get("statement", "")).strip()
                if stmt_text:
                    contexts_all.append(stmt_text)
            
            # Add summaries
            for sm in summaries:
                summary_text = str(sm.get("summary", "")).strip()
                if summary_text:
                    contexts_all.append(summary_text)
            
            # Add top entities (limit to 3 to avoid noise)
            if entities:
                scored = [e for e in entities if e.get("score") is not None]
                top_entities = (
                    sorted(scored, key=lambda x: x.get("score", 0), reverse=True)[:3]
                    if scored else entities[:3]
                )
                if top_entities:
                    summary_lines = []
                    for e in top_entities:
                        name = str(e.get("name", "")).strip()
                        etype = str(e.get("entity_type", "")).strip()
                        score = e.get("score")
                        if name:
                            meta = []
                            if etype:
                                meta.append(f"type={etype}")
                            if isinstance(score, (int, float)):
                                meta.append(f"score={score:.3f}")
                            summary_lines.append(
                                f"EntitySummary: {name}"
                                f"{(' [' + '; '.join(meta) + ']') if meta else ''}"
                            )
                    if summary_lines:
                        contexts_all.append("\n".join(summary_lines))
        
        elif search_type == "keyword":
            # Keyword-based search
            search_results = await search_graph(
                connector=connector,
                q=question,
                group_id=group_id,
                limit=search_limit
            )
            
            dialogs = search_results.get("dialogues", [])
            statements = search_results.get("statements", [])
            entities = search_results.get("entities", [])
            
            # Build context from dialogues
            for d in dialogs:
                content = str(d.get("content", "")).strip()
                if content:
                    contexts_all.append(content)
            
            # Add statements
            for s in statements:
                stmt_text = str(s.get("statement", "")).strip()
                if stmt_text:
                    contexts_all.append(stmt_text)
            
            # Add entity names
            if entities:
                entity_names = [
                    str(e.get("name", "")).strip()
                    for e in entities[:5]
                    if e.get("name")
                ]
                if entity_names:
                    contexts_all.append(f"EntitySummary: {', '.join(entity_names)}")
        
        else:  # hybrid
            # Hybrid search with fallback to embedding
            try:
                search_results = await run_hybrid_search(
                    query_text=question,
                    search_type=search_type,
                    group_id=group_id,
                    limit=search_limit,
                    include=["chunks", "statements", "entities", "summaries"],
                    output_path=None,
                )
                
                # Handle flat structure (new API format)
                if search_results and isinstance(search_results, dict):
                    chunks = search_results.get("chunks", [])
                    statements = search_results.get("statements", [])
                    entities = search_results.get("entities", [])
                    summaries = search_results.get("summaries", [])
                    
                    # Check if we got results
                    if not (chunks or statements or entities or summaries):
                        # Try nested structure (backward compatibility)
                        reranked = search_results.get("reranked_results", {})
                        if reranked and isinstance(reranked, dict):
                            chunks = reranked.get("chunks", [])
                            statements = reranked.get("statements", [])
                            entities = reranked.get("entities", [])
                            summaries = reranked.get("summaries", [])
                        else:
                            raise ValueError("Hybrid search returned empty results")
                else:
                    raise ValueError("Hybrid search returned empty results")
                
            except Exception as e:
                # Fallback to embedding search
                search_results = await search_graph_by_embedding(
                    connector=connector,
                    embedder_client=embedder,
                    query_text=question,
                    group_id=group_id,
                    limit=search_limit,
                    include=["chunks", "statements", "entities", "summaries"],
                )
                chunks = search_results.get("chunks", [])
                statements = search_results.get("statements", [])
                entities = search_results.get("entities", [])
                summaries = search_results.get("summaries", [])
            
            # Build context (same for both hybrid and fallback)
            for c in chunks:
                content = str(c.get("content", "")).strip()
                if content:
                    contexts_all.append(content)
            
            for s in statements:
                stmt_text = str(s.get("statement", "")).strip()
                if stmt_text:
                    contexts_all.append(stmt_text)
            
            for sm in summaries:
                summary_text = str(sm.get("summary", "")).strip()
                if summary_text:
                    contexts_all.append(summary_text)
            
            # Add top entities
            if entities:
                scored = [e for e in entities if e.get("score") is not None]
                top_entities = (
                    sorted(scored, key=lambda x: x.get("score", 0), reverse=True)[:3]
                    if scored else entities[:3]
                )
                if top_entities:
                    summary_lines = []
                    for e in top_entities:
                        name = str(e.get("name", "")).strip()
                        etype = str(e.get("entity_type", "")).strip()
                        score = e.get("score")
                        if name:
                            meta = []
                            if etype:
                                meta.append(f"type={etype}")
                            if isinstance(score, (int, float)):
                                meta.append(f"score={score:.3f}")
                            summary_lines.append(
                                f"EntitySummary: {name}"
                                f"{(' [' + '; '.join(meta) + ']') if meta else ''}"
                            )
                    if summary_lines:
                        contexts_all.append("\n".join(summary_lines))
    
    except Exception as e:
        # Return empty list on error
        contexts_all = []
    
    return contexts_all


async def ingest_conversations_if_needed(
    conversations: List[str],
    group_id: str,
    reset: bool = False
) -> bool:
    """
    Wrapper for conversation ingestion using external extraction pipeline.
    
    This function populates the Neo4j database with processed conversation data
    (chunks, statements, entities) so that the retrieval system has memory to search.
    
    The ingestion process:
    1. Parses conversation text into dialogue messages
    2. Chunks the dialogues into semantic units
    3. Extracts statements and entities using LLM
    4. Generates embeddings for all content
    5. Stores everything in Neo4j graph database
    
    Args:
        conversations: List of raw conversation texts from LoCoMo dataset
                      Example: ["User: I went to Paris. AI: When was that?", ...]
        group_id: Target group ID for database storage
        reset: Whether to clear existing data first (not implemented in wrapper)
        
    Returns:
        True if successful, False otherwise
        
    Note:
        The external function uses "contexts" to mean "conversation texts".
        This runs the full extraction pipeline: chunking → entity extraction → 
        statement extraction → embedding → Neo4j storage.
    """
    try:
        success = await ingest_contexts_via_full_pipeline(
            contexts=conversations,
            group_id=group_id,
            save_chunk_output=True
        )
        return success
    except Exception as e:
        print(f"[Ingestion] Failed to ingest conversations: {e}")
        return False
