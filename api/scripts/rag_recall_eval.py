#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "ragas==0.4.3",
#   "langchain-community==0.3.31",
# ]
# ///
"""Standalone MemoryBear retrieval dataset generator and recall evaluator.

The script intentionally imports no MemoryBear project modules. Run it with
``uv run rag_recall_eval.py --help`` or install the inline dependency manually.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
DEFAULT_TOKEN_ENV = "RAG_EVAL_JWT_TOKEN"
LLM_BASE_URL_ENV = "RAG_EVAL_LLM_BASE_URL"
LLM_API_KEY_ENV = "RAG_EVAL_LLM_API_KEY"
LLM_MODEL_ENV = "RAG_EVAL_LLM_MODEL"
ALLOWED_TARGET_FIELDS = {"kb_ids", "file_names_filter"}


class EvaluationError(RuntimeError):
    """Raised when an input or remote response makes a run invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise EvaluationError(f"Expected JSON object in {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvaluationError(
                    f"Invalid JSONL at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise EvaluationError(f"Expected JSON object at {path}:{line_number}")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row))
            handle.write("\n")


class ApiClient:
    """Minimal HTTP client for the public JSON contract used by this tool."""

    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        if query:
            path = f"{path}?{urllib.parse.urlencode(query, doseq=True)}"
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        url = f"{self.base_url}{path}"
        body = canonical_json(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise EvaluationError(
                f"HTTP {exc.code} from {method} {path}: {raw[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise EvaluationError(
                f"Request failed for {method} {path}: {exc.reason}"
            ) from exc

        if not 200 <= status < 300:
            raise EvaluationError(f"HTTP {status} from {method} {path}")
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"Non-JSON response from {method} {path}") from exc
        if not isinstance(envelope, dict):
            raise EvaluationError(f"Non-object response from {method} {path}")
        if envelope.get("code") != 0:
            raise EvaluationError(
                f"Business failure from {method} {path}: code={envelope.get('code')}, "
                f"message={envelope.get('msg')!r}"
            )
        return envelope.get("data")


def require_token(env_name: str) -> str:
    token = os.getenv(env_name, "").strip()
    if not token:
        raise EvaluationError(
            f"Missing authentication token in environment variable {env_name}"
        )
    return token


def paged_items(
    client: ApiClient,
    path: str,
    extra_query: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    page = 1
    rows: list[dict[str, Any]] = []
    while True:
        query = {"page": page, "pagesize": 100}
        if extra_query:
            query.update(extra_query)
        data = client.get(path, query)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise EvaluationError(f"Invalid paged response from {path}")
        rows.extend(item for item in data["items"] if isinstance(item, dict))
        page_info = data.get("page") or {}
        if not page_info.get("has_next"):
            break
        page += 1
    return rows


def flatten_chunk_items(
    items: list[dict[str, Any]],
    kb_id: str,
    document_id: str,
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        parent = dict(item)
        children = parent.pop("children", None) or []
        flattened.append(normalize_snapshot_chunk(parent, kb_id, document_id))
        for child in children:
            if isinstance(child, dict):
                flattened.append(normalize_snapshot_chunk(child, kb_id, document_id))
    return flattened


def normalize_snapshot_chunk(
    item: dict[str, Any], kb_id: str, document_id: str
) -> dict[str, Any]:
    metadata = dict(item.get("metadata") or {})
    metadata.setdefault("knowledge_id", kb_id)
    metadata.setdefault("document_id", document_id)
    content = item.get("page_content")
    if not isinstance(content, str):
        content = "" if content is None else str(content)
    chunk_type = str(metadata.get("chunk_type") or "chunk")
    physical_id = str(metadata.get("doc_id") or "")
    if chunk_type == "child":
        canonical_id = str(metadata.get("parent_id") or physical_id)
    elif chunk_type == "qa":
        canonical_id = str(metadata.get("source_chunk_id") or physical_id)
    else:
        canonical_id = physical_id
    return {
        "knowledge_id": str(metadata.get("knowledge_id") or kb_id),
        "document_id": str(metadata.get("document_id") or document_id),
        "physical_chunk_id": physical_id,
        "canonical_evidence_id": canonical_id,
        "chunk_type": chunk_type,
        "parent_id": str(metadata.get("parent_id") or ""),
        "source_chunk_id": str(metadata.get("source_chunk_id") or ""),
        "sort_id": metadata.get("sort_id"),
        "file_name": str(metadata.get("file_name") or ""),
        "page_content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "metadata": metadata,
    }


def corpus_hash_payload(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for chunk in snapshot.get("chunks") or []:
        rows.append(
            {
                "knowledge_id": chunk.get("knowledge_id"),
                "document_id": chunk.get("document_id"),
                "physical_chunk_id": chunk.get("physical_chunk_id"),
                "canonical_evidence_id": chunk.get("canonical_evidence_id"),
                "chunk_type": chunk.get("chunk_type"),
                "parent_id": chunk.get("parent_id"),
                "source_chunk_id": chunk.get("source_chunk_id"),
                "sort_id": chunk.get("sort_id"),
                "content_sha256": chunk.get("content_sha256"),
            }
        )
    return sorted(rows, key=canonical_json)


def snapshot_command(args: argparse.Namespace) -> int:
    client = ApiClient(args.base_url, require_token(args.token_env), args.timeout)
    knowledges: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []

    for kb_id in args.kb_id:
        knowledge = client.get(f"/api/knowledges/{urllib.parse.quote(kb_id)}")
        if not isinstance(knowledge, dict):
            raise EvaluationError(f"Invalid knowledge response for {kb_id}")
        if knowledge.get("status") != 1:
            raise EvaluationError(f"Knowledge base {kb_id} is not active")
        if knowledge.get("type") == "Folder":
            raise EvaluationError(f"Folder knowledge base {kb_id} is not supported")
        knowledges.append(knowledge)

        kb_documents = paged_items(
            client,
            f"/api/documents/{urllib.parse.quote(kb_id)}/documents",
            {"orderby": "id", "desc": "false"},
        )
        for document in kb_documents:
            document_id = str(document.get("id") or "")
            if not document_id:
                raise EvaluationError(f"Document without id in knowledge base {kb_id}")
            if not args.include_unready:
                if (
                    document.get("status") != 1
                    or document.get("progress") != 1
                    or document.get("run") != 0
                ):
                    raise EvaluationError(
                        f"Document {document_id} is not ready: status={document.get('status')}, "
                        f"progress={document.get('progress')}, run={document.get('run')}"
                    )
            documents.append({**document, "id": document_id, "kb_id": kb_id})
            chunk_items = paged_items(
                client,
                f"/api/chunks/{urllib.parse.quote(kb_id)}/{urllib.parse.quote(document_id)}/chunks",
            )
            chunks.extend(flatten_chunk_items(chunk_items, kb_id, document_id))

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "source": {"base_url": args.base_url.rstrip("/"), "auth_mode": "jwt"},
        "knowledges": knowledges,
        "documents": documents,
        "chunks": chunks,
    }
    snapshot["physical_corpus_sha256"] = sha256_json(corpus_hash_payload(snapshot))
    write_json(args.output, snapshot)
    print(
        canonical_json(
            {
                "output": str(args.output),
                "knowledge_count": len(knowledges),
                "document_count": len(documents),
                "chunk_count": len(chunks),
                "physical_corpus_sha256": snapshot["physical_corpus_sha256"],
            }
        )
    )
    return 0


def parse_llm_json(raw: str) -> dict[str, Any]:
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, flags=re.DOTALL)
        if not match:
            raise EvaluationError("LLM did not return a JSON object")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise EvaluationError("LLM response is not a JSON object")
    return parsed


class OpenAICompatibleGenerator:
    """Small OpenAI-compatible chat-completions client used only for generation."""

    def __init__(self, timeout: float, temperature: float):
        base_url = os.getenv(LLM_BASE_URL_ENV, "").strip().rstrip("/")
        api_key = os.getenv(LLM_API_KEY_ENV, "").strip()
        model = os.getenv(LLM_MODEL_ENV, "").strip()
        missing = [
            name
            for name, value in (
                (LLM_BASE_URL_ENV, base_url),
                (LLM_API_KEY_ENV, api_key),
                (LLM_MODEL_ENV, model),
            )
            if not value
        ]
        if missing:
            raise EvaluationError(
                f"Missing LLM environment variables: {', '.join(missing)}"
            )
        self.url = (
            base_url
            if base_url.endswith("/chat/completions")
            else f"{base_url}/chat/completions"
        )
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature

    def generate(
        self, evidence: list[dict[str, str]], no_hit: bool = False
    ) -> dict[str, str]:
        if no_hit:
            task = (
                "Generate one natural user question that is unrelated to and cannot be answered by the provided "
                "knowledge-base excerpts. Return JSON with query and an empty reference."
            )
        elif len(evidence) > 1:
            task = (
                "Generate one natural user question that requires combining every provided excerpt. Avoid copying "
                "long phrases. Return JSON with query and a concise reference supported only by the excerpts."
            )
        else:
            task = (
                "Generate one natural user question answerable from the excerpt. Paraphrase the wording and avoid "
                "mentioning documents or excerpts. Return JSON with query and a concise reference."
            )
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "You create retrieval benchmark queries. Output a JSON object only.",
                },
                {
                    "role": "user",
                    "content": f"{task}\n\nEvidence:\n{json.dumps(evidence, ensure_ascii=False)}",
                },
            ],
        }
        request = urllib.request.Request(
            self.url,
            data=canonical_json(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise EvaluationError(f"LLM generation failed: {exc}") from exc
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise EvaluationError("Unexpected LLM response contract") from exc
        parsed = parse_llm_json(content)
        query = str(parsed.get("query") or "").strip()
        reference = str(parsed.get("reference") or "").strip()
        if not query:
            raise EvaluationError("LLM returned an empty query")
        return {"query": query, "reference": reference}


def context_id(chunk: dict[str, Any]) -> str:
    return ":".join(
        [
            str(chunk.get("knowledge_id") or ""),
            str(chunk.get("document_id") or ""),
            str(
                chunk.get("canonical_evidence_id")
                or chunk.get("physical_chunk_id")
                or ""
            ),
        ]
    )


def document_context_id(chunk: dict[str, Any]) -> str:
    return f"{chunk.get('knowledge_id')}:{chunk.get('document_id')}"


def eligible_source_chunks(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    parent_ids = {
        str(chunk.get("parent_id"))
        for chunk in snapshot.get("chunks") or []
        if chunk.get("chunk_type") == "child" and chunk.get("parent_id")
    }
    rows = []
    for chunk in snapshot.get("chunks") or []:
        chunk_type = chunk.get("chunk_type") or "chunk"
        content = str(chunk.get("page_content") or "").strip()
        if not content or chunk_type == "qa":
            continue
        if chunk_type == "parent" and str(chunk.get("physical_chunk_id")) in parent_ids:
            continue
        if not chunk.get("physical_chunk_id") or not chunk.get("canonical_evidence_id"):
            continue
        rows.append(chunk)
    return rows


def source_provenance(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "knowledge_id": chunk.get("knowledge_id"),
        "document_id": chunk.get("document_id"),
        "physical_chunk_id": chunk.get("physical_chunk_id"),
        "canonical_evidence_id": chunk.get("canonical_evidence_id"),
        "chunk_type": chunk.get("chunk_type"),
        "content_sha256": chunk.get("content_sha256"),
    }


def build_generated_case(
    index: int,
    generated: dict[str, str],
    sources: list[dict[str, Any]],
    snapshot: dict[str, Any],
    model: str,
    seed: int,
    no_hit: bool,
    kb_document_counts: dict[str, int],
    file_name_counts: dict[tuple[str, str], int],
) -> dict[str, Any]:
    if no_hit:
        kb_ids = sorted(
            str(item.get("id")) for item in snapshot.get("knowledges") or []
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": f"no-hit-{index:05d}",
            "query": generated["query"],
            "reference": "",
            "corpus_mode": "no_hit",
            "target": {"kb_ids": kb_ids},
            "gold_document_ids": [],
            "gold_context_ids": [],
            "required_context_groups": [],
            "alternative_context_groups": [],
            "expected_no_hit": True,
            "load_weight": 1,
            "tags": ["generated", "no-hit", "low-confidence"],
            "physical_corpus_sha256": snapshot["physical_corpus_sha256"],
            "provenance": {"sources": [], "strategy": "out_of_scope_generation"},
            "generation": {"model": model, "seed": seed, "generated_at": utc_now()},
            "quality_status": "generated_unreviewed",
        }

    kb_ids = sorted({str(source.get("knowledge_id")) for source in sources})
    gold_documents = sorted({document_context_id(source) for source in sources})
    gold_contexts = list(dict.fromkeys(context_id(source) for source in sources))
    target: dict[str, Any] = {"kb_ids": kb_ids}
    if len(sources) > 1:
        corpus_mode = "multi_document"
    else:
        source = sources[0]
        kb_id = str(source.get("knowledge_id"))
        file_name = str(source.get("file_name") or "")
        if kb_document_counts[kb_id] == 1:
            corpus_mode = "single_document_kb"
        else:
            corpus_mode = "single_document_filter"
            target["file_names_filter"] = [file_name]
            if file_name_counts[(kb_id, file_name)] != 1:
                raise EvaluationError(
                    f"File name {file_name!r} is not unique in knowledge base {kb_id}"
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": f"{corpus_mode}-{index:05d}",
        "query": generated["query"],
        "reference": generated["reference"],
        "corpus_mode": corpus_mode,
        "target": target,
        "gold_document_ids": gold_documents,
        "gold_context_ids": gold_contexts,
        "required_context_groups": [gold_contexts],
        "alternative_context_groups": [],
        "expected_no_hit": False,
        "load_weight": 1,
        "tags": ["generated", "multi-source" if len(sources) > 1 else "single-source"],
        "physical_corpus_sha256": snapshot["physical_corpus_sha256"],
        "provenance": {"sources": [source_provenance(source) for source in sources]},
        "generation": {"model": model, "seed": seed, "generated_at": utc_now()},
        "quality_status": "generated_unreviewed",
    }


def generate_command(args: argparse.Namespace) -> int:
    snapshot = read_json(args.snapshot)
    expected_hash = sha256_json(corpus_hash_payload(snapshot))
    if snapshot.get("physical_corpus_sha256") != expected_hash:
        raise EvaluationError("Snapshot content does not match physical_corpus_sha256")
    sources = eligible_source_chunks(snapshot)
    if not sources:
        raise EvaluationError("Snapshot has no eligible non-QA source chunks")

    documents = snapshot.get("documents") or []
    kb_document_counts: dict[str, int] = defaultdict(int)
    file_name_counts: dict[tuple[str, str], int] = defaultdict(int)
    for document in documents:
        kb_id = str(document.get("kb_id") or "")
        file_name = str(document.get("file_name") or "")
        kb_document_counts[kb_id] += 1
        file_name_counts[(kb_id, file_name)] += 1

    by_document: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    single_eligible: list[dict[str, Any]] = []
    for source in sources:
        kb_id = str(source.get("knowledge_id"))
        document_id = str(source.get("document_id"))
        by_document[(kb_id, document_id)].append(source)
        file_name = str(source.get("file_name") or "")
        if kb_document_counts[kb_id] == 1 or file_name_counts[(kb_id, file_name)] == 1:
            single_eligible.append(source)
    if not single_eligible:
        raise EvaluationError(
            "No source chunk can form an unambiguous single-document case"
        )

    rng = random.Random(args.seed)
    generator = OpenAICompatibleGenerator(args.timeout, args.temperature)
    no_hit_count = min(args.count, round(args.count * args.no_hit_ratio))
    remaining = args.count - no_hit_count
    multi_count = min(remaining, round(remaining * args.multi_ratio))
    single_count = remaining - multi_count
    if multi_count and len(by_document) < 2:
        raise EvaluationError(
            "At least two documents are required for multi-document generation"
        )

    plans: list[tuple[list[dict[str, Any]], bool]] = []
    for _ in range(single_count):
        plans.append(([rng.choice(single_eligible)], False))
    document_keys = list(by_document)
    for _ in range(multi_count):
        selected_keys = rng.sample(document_keys, 2)
        plans.append(([rng.choice(by_document[key]) for key in selected_keys], False))
    for _ in range(no_hit_count):
        context_sample = rng.sample(sources, min(5, len(sources)))
        plans.append((context_sample, True))
    rng.shuffle(plans)

    cases: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    for index, (selected, no_hit) in enumerate(plans, start=1):
        evidence = [
            {
                "source": f"source-{position + 1}",
                "text": str(source.get("page_content") or "")[: args.max_source_chars],
            }
            for position, source in enumerate(selected)
        ]
        generated = generator.generate(evidence, no_hit=no_hit)
        query_key = generated["query"].casefold()
        if query_key in seen_queries:
            raise EvaluationError(f"Duplicate generated query at case {index}")
        seen_queries.add(query_key)
        cases.append(
            build_generated_case(
                index=index,
                generated=generated,
                sources=[] if no_hit else selected,
                snapshot=snapshot,
                model=generator.model,
                seed=args.seed,
                no_hit=no_hit,
                kb_document_counts=kb_document_counts,
                file_name_counts=file_name_counts,
            )
        )
        print(f"generated {index}/{len(plans)}", file=sys.stderr)

    write_jsonl(args.output, cases)
    print(
        canonical_json(
            {
                "output": str(args.output),
                "case_count": len(cases),
                "single_count": single_count,
                "multi_count": multi_count,
                "no_hit_count": no_hit_count,
                "physical_corpus_sha256": snapshot["physical_corpus_sha256"],
            }
        )
    )
    return 0


def validate_cases(
    cases: list[dict[str, Any]], snapshot: dict[str, Any] | None = None
) -> list[str]:
    errors: list[str] = []
    if not cases:
        errors.append("dataset is empty")
        return errors
    seen_ids: set[str] = set()
    snapshot_hash = snapshot.get("physical_corpus_sha256") if snapshot else None
    valid_context_ids = {
        context_id(chunk) for chunk in (snapshot or {}).get("chunks") or []
    }
    for index, case in enumerate(cases, start=1):
        prefix = f"case[{index}]"
        case_id = str(case.get("case_id") or "")
        if not case_id:
            errors.append(f"{prefix}: missing case_id")
        elif case_id in seen_ids:
            errors.append(f"{prefix}: duplicate case_id {case_id}")
        seen_ids.add(case_id)
        if case.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{prefix}: unsupported schema_version")
        if not str(case.get("query") or "").strip():
            errors.append(f"{prefix}: empty query")
        target = case.get("target")
        if not isinstance(target, dict) or not target.get("kb_ids"):
            errors.append(f"{prefix}: target.kb_ids must be non-empty")
        elif unknown := set(target) - ALLOWED_TARGET_FIELDS:
            errors.append(f"{prefix}: unknown target fields {sorted(unknown)}")
        gold = case.get("gold_context_ids") or []
        if case.get("expected_no_hit"):
            if gold:
                errors.append(
                    f"{prefix}: no-hit case must not contain gold_context_ids"
                )
        elif not gold:
            errors.append(f"{prefix}: ordinary case needs gold_context_ids")
        if snapshot_hash and case.get("physical_corpus_sha256") != snapshot_hash:
            errors.append(f"{prefix}: snapshot hash mismatch")
        if snapshot and not case.get("expected_no_hit"):
            missing = sorted(set(gold) - valid_context_ids)
            if missing:
                errors.append(
                    f"{prefix}: gold context ids missing from snapshot: {missing}"
                )
    return errors


def validate_command(args: argparse.Namespace) -> int:
    cases = read_jsonl(args.dataset)
    snapshot = read_json(args.snapshot) if args.snapshot else None
    errors = validate_cases(cases, snapshot)
    result = {"valid": not errors, "case_count": len(cases), "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 3


def ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def canonical_result_ids(item: dict[str, Any]) -> tuple[str, str]:
    metadata = item.get("metadata") or {}
    kb_id = str(metadata.get("knowledge_id") or "")
    document_id = str(metadata.get("document_id") or "")
    chunk_type = str(metadata.get("chunk_type") or "chunk")
    physical_id = str(metadata.get("doc_id") or "")
    if chunk_type == "child":
        evidence_id = str(metadata.get("parent_id") or physical_id)
    elif chunk_type == "qa":
        evidence_id = str(metadata.get("source_chunk_id") or physical_id)
    else:
        evidence_id = physical_id
    document_result_id = f"{kb_id}:{document_id}" if kb_id and document_id else ""
    context_result_id = (
        f"{kb_id}:{document_id}:{evidence_id}"
        if document_result_id and evidence_id
        else ""
    )
    return document_result_id, context_result_id


def precision(retrieved: list[str], gold: set[str]) -> float | None:
    return len(set(retrieved) & gold) / len(set(retrieved)) if retrieved else None


def recall(retrieved: list[str], gold: set[str]) -> float:
    return len(set(retrieved) & gold) / len(gold) if gold else 0.0


def reciprocal_rank(retrieved: list[str], gold: set[str]) -> float:
    for rank, item in enumerate(retrieved, start=1):
        if item in gold:
            return 1.0 / rank
    return 0.0


def ndcg(retrieved: list[str], gold: set[str]) -> float:
    if not gold:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(retrieved, start=1)
        if item in gold
    )
    ideal_count = min(len(gold), len(retrieved))
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / ideal if ideal else 0.0


def group_scores(retrieved: list[str], case: dict[str, Any]) -> tuple[float, bool]:
    required_groups = case.get("required_context_groups") or []
    alternatives = case.get("alternative_context_groups") or []
    groups = [set(group) for group in required_groups]
    groups.extend(set(group) for group in alternatives)
    groups = [group for group in groups if group]
    if not groups:
        return 0.0, False
    retrieved_set = set(retrieved)
    scores = [len(retrieved_set & group) / len(group) for group in groups]
    return max(scores), any(group <= retrieved_set for group in groups)


async def ragas_id_scores(retrieved: list[str], gold: list[str]) -> tuple[float, float]:
    from ragas import SingleTurnSample

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Importing IDBasedContext")
        from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall

    sample = SingleTurnSample(
        retrieved_context_ids=retrieved, reference_context_ids=gold
    )
    ragas_precision = await IDBasedContextPrecision().single_turn_ascore(sample)
    ragas_recall = await IDBasedContextRecall().single_turn_ascore(sample)
    return float(ragas_precision), float(ragas_recall)


METRIC_LABELS = {
    "context_precision": "Chunk 精确率",
    "context_recall": "Chunk 召回率",
    "context_hit": "Chunk 命中率",
    "context_mrr": "Chunk MRR",
    "context_ndcg": "Chunk nDCG",
    "document_recall": "文档召回率",
    "document_hit": "文档命中率",
    "group_recall": "证据组覆盖率",
    "complete_evidence_group": "完整证据组命中率",
    "ragas_id_context_precision": "Ragas ID 精确率",
    "ragas_id_context_recall": "Ragas ID 召回率",
}

CORPUS_MODE_LABELS = {
    "single_document_kb": "单文档知识库",
    "single_document_filter": "单文档过滤",
    "multi_document": "多文档",
    "no_hit": "无答案",
}


def html_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def format_percent(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def format_number(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def metric_keys_in_order(metrics: dict[str, Any]) -> list[str]:
    return sorted(metrics, key=lambda value: int(value.lstrip("@")))


def average_detail_metric(
    rows: list[dict[str, Any]], key: str, metric: str
) -> float | None:
    values = [
        item["metrics"][key][metric]
        for item in rows
        if isinstance(item.get("metrics", {}).get(key), dict)
        and item["metrics"][key].get(metric) is not None
    ]
    if not values:
        return None
    return sum(float(value) for value in values) / len(values)


def render_metric_table(
    title: str,
    metric_names: list[str],
    summary_metrics: dict[str, Any],
) -> str:
    headers = "".join(
        f"<th>{html_escape(METRIC_LABELS[name])}</th>" for name in metric_names
    )
    rows = []
    for key in metric_keys_in_order(summary_metrics):
        values = summary_metrics[key]
        cells = "".join(
            f"<td>{format_percent(values.get(name))}</td>" for name in metric_names
        )
        rows.append(f"<tr><th>K={html_escape(key.lstrip('@'))}</th>{cells}</tr>")
    return (
        f'<section><h2>{html_escape(title)}</h2><div class="table-wrap"><table>'
        f"<thead><tr><th>截断位置</th>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def render_recall_report(
    summary: dict[str, Any], details: list[dict[str, Any]], output: Path
) -> None:
    summary_metrics = summary.get("metrics") or {}
    metric_keys = metric_keys_in_order(summary_metrics)
    display_key = metric_keys[-1] if metric_keys else None
    display_label = f"K={display_key.lstrip('@')}" if display_key else "无 K 值"
    request_failures = int(summary.get("request_failure_count") or 0)
    ragas_failures = int(summary.get("ragas_failure_count") or 0)
    status_ok = request_failures == 0 and ragas_failures == 0
    status_text = "评测运行完成" if status_ok else "评测存在异常"
    status_class = "good" if status_ok else "bad"
    no_hit_count = sum(1 for item in details if item.get("corpus_mode") == "no_hit")

    metric_sections = []
    if summary_metrics:
        metric_sections.extend(
            [
                render_metric_table(
                    "Chunk 召回质量",
                    [
                        "context_precision",
                        "context_recall",
                        "context_hit",
                        "context_mrr",
                        "context_ndcg",
                    ],
                    summary_metrics,
                ),
                render_metric_table(
                    "文档与多证据召回质量",
                    [
                        "document_recall",
                        "document_hit",
                        "group_recall",
                        "complete_evidence_group",
                    ],
                    summary_metrics,
                ),
                render_metric_table(
                    "Ragas ID 指标",
                    ["ragas_id_context_precision", "ragas_id_context_recall"],
                    summary_metrics,
                ),
            ]
        )

    group_rows = []
    ordered_modes = [
        "single_document_kb",
        "single_document_filter",
        "multi_document",
        "no_hit",
    ]
    for mode in ordered_modes:
        rows = [item for item in details if item.get("corpus_mode") == mode]
        if not rows:
            continue
        successful = sum(bool(item.get("request_success")) for item in rows)
        if mode == "no_hit":
            no_hit_values = [
                item.get("metrics", {}).get("no_hit_correct") for item in rows
            ]
            accuracy = sum(bool(value) for value in no_hit_values) / len(no_hit_values)
            group_rows.append(
                "<tr>"
                f"<td>{html_escape(CORPUS_MODE_LABELS[mode])}</td><td>{len(rows)}</td><td>{successful}</td>"
                f'<td colspan="4">无答案准确率 {format_percent(accuracy)}</td>'
                "</tr>"
            )
            continue
        key = display_key or ""
        group_rows.append(
            "<tr>"
            f"<td>{html_escape(CORPUS_MODE_LABELS.get(mode, mode))}</td><td>{len(rows)}</td><td>{successful}</td>"
            f"<td>{format_percent(average_detail_metric(rows, key, 'context_hit'))}</td>"
            f"<td>{format_percent(average_detail_metric(rows, key, 'context_recall'))}</td>"
            f"<td>{format_percent(average_detail_metric(rows, key, 'document_hit'))}</td>"
            f"<td>{format_percent(average_detail_metric(rows, key, 'complete_evidence_group'))}</td>"
            "</tr>"
        )

    case_rows = []
    for item in details:
        mode = str(item.get("corpus_mode") or "unknown")
        metrics = item.get("metrics") or {}
        if mode == "no_hit":
            quality = "正确无结果" if metrics.get("no_hit_correct") else "误召回"
            hit = "—"
            mrr = "—"
            complete = "—"
        else:
            row = metrics.get(display_key, {}) if display_key else {}
            quality = "命中" if row.get("context_hit") else "未命中"
            hit = format_percent(row.get("context_hit"))
            mrr = format_number(row.get("context_mrr"), 3)
            complete = format_percent(row.get("complete_evidence_group"))
        request_status = "成功" if item.get("request_success") else "失败"
        request_class = "good-text" if item.get("request_success") else "bad-text"
        case_rows.append(
            "<tr>"
            f"<td><code>{html_escape(item.get('case_id'))}</code></td>"
            f"<td>{html_escape(CORPUS_MODE_LABELS.get(mode, mode))}</td>"
            f'<td class="{request_class}">{request_status}</td>'
            f"<td>{format_number(item.get('elapsed_ms'), 1)} ms</td>"
            f"<td>{int(item.get('result_count') or 0)}</td>"
            f"<td>{html_escape(quality)}</td><td>{hit}</td><td>{mrr}</td><td>{complete}</td>"
            "</tr>"
        )

    retrieval = summary.get("retrieval") or {}
    rerank_threshold = retrieval.get("rerank_score_threshold")
    rerank_label = "未设置" if rerank_threshold is None else rerank_threshold
    report = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>知识库召回效果评测报告</title>
<style>
:root {{ color-scheme: light; --ink:#172033; --muted:#637083; --line:#dce3ed; --panel:#f7f9fc; --good:#137a4b; --bad:#b42318; --accent:#3157d5; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:#eef2f7; color:var(--ink); font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
main {{ width:min(1280px, calc(100% - 32px)); margin:32px auto; }}
header, section {{ background:white; border:1px solid var(--line); border-radius:14px; padding:22px; margin-bottom:16px; box-shadow:0 6px 24px rgba(25,41,72,.05); }}
h1 {{ margin:0 0 8px; font-size:28px; }} h2 {{ margin:0 0 14px; font-size:19px; }}
p {{ margin:6px 0; }} .muted {{ color:var(--muted); }}
.status {{ display:inline-block; padding:4px 10px; border-radius:999px; font-weight:700; }}
.status.good {{ color:var(--good); background:#e8f7ef; }} .status.bad {{ color:var(--bad); background:#ffebe9; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-top:18px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px; }}
.card b {{ display:block; font-size:23px; margin-top:3px; }}
.table-wrap {{ overflow:auto; }} table {{ width:100%; border-collapse:collapse; white-space:nowrap; }}
th,td {{ border-bottom:1px solid var(--line); padding:10px 12px; text-align:right; }} th:first-child,td:first-child {{ text-align:left; }}
thead th {{ background:var(--panel); color:#3c4960; position:sticky; top:0; }}
code {{ font-family:"SFMono-Regular",Consolas,monospace; font-size:12px; }}
.good-text {{ color:var(--good); font-weight:650; }} .bad-text {{ color:var(--bad); font-weight:650; }}
.params {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:8px 18px; }}
.params div {{ border-bottom:1px dashed var(--line); padding:6px 0; }}
dl {{ display:grid; grid-template-columns:minmax(180px,260px) 1fr; gap:8px 18px; }} dt {{ font-weight:700; }} dd {{ margin:0; color:var(--muted); }}
</style>
</head>
<body><main>
<header>
  <span class="status {status_class}">{status_text}</span>
  <h1>知识库召回效果评测报告</h1>
  <p class="muted">生成时间：{html_escape(summary.get("created_at"))}，本报告只评测检索与召回，不包含最终答案评测。</p>
  <div class="cards">
    <div class="card">评测 case<b>{int(summary.get("case_count") or 0)}</b></div>
    <div class="card">请求成功<b>{int(summary.get("request_success_count") or 0)}</b></div>
    <div class="card">请求失败<b>{request_failures}</b></div>
    <div class="card">Ragas 计算失败<b>{ragas_failures}</b></div>
    <div class="card">no-hit 样本<b>{no_hit_count}</b></div>
    <div class="card">no-hit 准确率<b>{format_percent(summary.get("no_hit_accuracy"))}</b></div>
  </div>
</header>
<section><h2>运行参数</h2><div class="params">
  <div>数据集：<code>{html_escape(summary.get("dataset"))}</code></div>
  <div>数据集 SHA256：<code>{html_escape(summary.get("dataset_sha256"))}</code></div>
  <div>语料 SHA256：<code>{html_escape(summary.get("physical_corpus_sha256"))}</code></div>
  <div>检索方式：{html_escape(retrieval.get("retrieve_type"))}</div>
  <div>top_k / top_n：{html_escape(retrieval.get("top_k"))} / {html_escape(retrieval.get("top_n"))}</div>
  <div>相似度阈值：{html_escape(retrieval.get("similarity_threshold"))}</div>
  <div>向量权重：{html_escape(retrieval.get("vector_similarity_weight"))}</div>
  <div>重排阈值：{html_escape(rerank_label)}</div>
</div></section>
{"".join(metric_sections)}
<section><h2>分场景结果（{html_escape(display_label)}）</h2><div class="table-wrap"><table>
<thead><tr><th>场景</th><th>case 数</th><th>请求成功</th><th>Chunk 命中率</th><th>Chunk 召回率</th><th>文档命中率</th><th>完整证据组</th></tr></thead>
<tbody>{"".join(group_rows)}</tbody></table></div></section>
<section><h2>逐 case 结果（{html_escape(display_label)}）</h2><div class="table-wrap"><table>
<thead><tr><th>case ID</th><th>场景</th><th>请求</th><th>耗时</th><th>返回数</th><th>判定</th><th>Hit</th><th>MRR</th><th>完整证据组</th></tr></thead>
<tbody>{"".join(case_rows)}</tbody></table></div></section>
<section><h2>指标怎么看</h2><dl>
  <dt>Chunk 精确率</dt><dd>前 K 个召回 chunk 中，黄金 chunk 所占比例。返回了很多无关 chunk 时会下降。</dd>
  <dt>Chunk 召回率</dt><dd>黄金 chunk 中有多少被前 K 个结果覆盖。</dd>
  <dt>Chunk 命中率</dt><dd>每个 case 前 K 个结果至少命中一个黄金 chunk 的比例。</dd>
  <dt>MRR</dt><dd>第一个黄金 chunk 排名的倒数，越靠前越接近 100%。</dd>
  <dt>nDCG</dt><dd>对多个黄金 chunk 的排名质量进行位置折损计算。</dd>
  <dt>完整证据组</dt><dd>多文档问题所需的一整组证据是否都被召回。</dd>
  <dt>Ragas ID 指标</dt><dd>Ragas 根据召回 context ID 与黄金 context ID 的集合关系计算的精确率和召回率。</dd>
  <dt>no-hit 准确率</dt><dd>对预期知识库无法回答的问题，接口正确返回空列表的比例。</dd>
</dl></section>
<section><h2>数据局限</h2><p>黄金证据来自生成问题时选中的 source chunk，未自动穷举知识库中所有等价 chunk。`generated_unreviewed` 数据集适合建立基线和参数对比，不等同于经人工审核的绝对正确率。</p></section>
</main></body></html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")


def evaluate_command(args: argparse.Namespace) -> int:
    cases = read_jsonl(args.dataset)
    snapshot = read_json(args.snapshot) if args.snapshot else None
    errors = validate_cases(cases, snapshot)
    if errors:
        raise EvaluationError("Dataset validation failed:\n" + "\n".join(errors))
    client = ApiClient(args.base_url, require_token(args.token_env), args.timeout)
    k_values = sorted(set(args.k or [1, 3, 5, 10]))
    if not k_values or min(k_values) < 1:
        raise EvaluationError("At least one positive K value is required")
    if max(k_values) > args.top_k:
        raise EvaluationError("max(K) must not exceed top_k")

    details: list[dict[str, Any]] = []
    failed_requests = 0
    ragas_failures = 0
    with asyncio.Runner() as runner:
        for index, case in enumerate(cases, start=1):
            payload = {
                "query": case["query"],
                **case["target"],
                "retrieve_type": args.retrieve_type,
                "top_k": args.top_k,
                "top_n": args.top_n,
                "similarity_threshold": args.similarity_threshold,
                "vector_similarity_weight": args.vector_similarity_weight,
                "rerank_score_threshold": args.rerank_score_threshold,
            }
            payload = {
                key: value for key, value in payload.items() if value is not None
            }
            started = time.perf_counter()
            try:
                response_items = client.post("/api/chunks/retrieval", payload)
                if not isinstance(response_items, list):
                    raise EvaluationError("Retrieval data is not a list")
                request_error = None
            except EvaluationError as exc:
                response_items = []
                request_error = str(exc)
                failed_requests += 1
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            document_ids: list[str] = []
            context_ids: list[str] = []
            invalid_provenance = 0
            for item in response_items:
                if not isinstance(item, dict):
                    invalid_provenance += 1
                    continue
                document_id, context_result_id = canonical_result_ids(item)
                if not document_id or not context_result_id:
                    invalid_provenance += 1
                    continue
                document_ids.append(document_id)
                context_ids.append(context_result_id)
            document_ids = ordered_unique(document_ids)
            context_ids = ordered_unique(context_ids)
            result: dict[str, Any] = {
                "case_id": case["case_id"],
                "corpus_mode": case["corpus_mode"],
                "request_success": request_error is None,
                "request_error": request_error,
                "elapsed_ms": elapsed_ms,
                "result_count": len(response_items),
                "invalid_provenance_count": invalid_provenance,
                "retrieved_document_ids": document_ids,
                "retrieved_context_ids": context_ids,
                "metrics": {},
            }
            if case.get("expected_no_hit"):
                result["metrics"]["no_hit_correct"] = (
                    request_error is None and not response_items
                )
            else:
                gold_contexts = list(case.get("gold_context_ids") or [])
                gold_context_set = set(gold_contexts)
                gold_document_set = set(case.get("gold_document_ids") or [])
                for k in k_values:
                    retrieved_contexts = context_ids[:k]
                    retrieved_documents = document_ids[:k]
                    group_recall, complete_group = group_scores(
                        retrieved_contexts, case
                    )
                    metric_row: dict[str, Any] = {
                        "context_precision": precision(
                            retrieved_contexts, gold_context_set
                        ),
                        "context_recall": recall(retrieved_contexts, gold_context_set),
                        "context_hit": bool(set(retrieved_contexts) & gold_context_set),
                        "context_mrr": reciprocal_rank(
                            retrieved_contexts, gold_context_set
                        ),
                        "context_ndcg": ndcg(retrieved_contexts, gold_context_set),
                        "document_recall": recall(
                            retrieved_documents, gold_document_set
                        ),
                        "document_hit": bool(
                            set(retrieved_documents) & gold_document_set
                        ),
                        "group_recall": group_recall,
                        "complete_evidence_group": complete_group,
                    }
                    try:
                        ragas_precision, ragas_recall = runner.run(
                            ragas_id_scores(retrieved_contexts, gold_contexts)
                        )
                        metric_row["ragas_id_context_precision"] = ragas_precision
                        metric_row["ragas_id_context_recall"] = ragas_recall
                    except Exception as exc:  # noqa: BLE001 - metric failure belongs in the report
                        metric_row["ragas_error"] = f"{type(exc).__name__}: {exc}"
                        ragas_failures += 1
                    result["metrics"][f"@{k}"] = metric_row
            details.append(result)
            print(f"evaluated {index}/{len(cases)}", file=sys.stderr)

    summary_metrics: dict[str, Any] = {}
    for k in k_values:
        key = f"@{k}"
        rows = [
            item["metrics"].get(key) for item in details if item["metrics"].get(key)
        ]
        if not rows:
            continue
        metric_names = sorted(
            {
                name
                for row in rows
                for name, value in row.items()
                if isinstance(value, (int, float, bool))
            }
        )
        summary_metrics[key] = {
            name: sum(float(row[name]) for row in rows if row.get(name) is not None)
            / sum(1 for row in rows if row.get(name) is not None)
            for name in metric_names
            if any(row.get(name) is not None for row in rows)
        }
    no_hit_rows = [item for item in details if "no_hit_correct" in item["metrics"]]
    summary = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "dataset": str(args.dataset),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "physical_corpus_sha256": cases[0].get("physical_corpus_sha256")
        if cases
        else None,
        "case_count": len(cases),
        "request_success_count": len(cases) - failed_requests,
        "request_failure_count": failed_requests,
        "ragas_failure_count": ragas_failures,
        "no_hit_accuracy": (
            sum(bool(item["metrics"]["no_hit_correct"]) for item in no_hit_rows)
            / len(no_hit_rows)
            if no_hit_rows
            else None
        ),
        "retrieval": {
            "retrieve_type": args.retrieve_type,
            "top_k": args.top_k,
            "top_n": args.top_n,
            "similarity_threshold": args.similarity_threshold,
            "vector_similarity_weight": args.vector_similarity_weight,
            "rerank_score_threshold": args.rerank_score_threshold,
        },
        "metrics": summary_metrics,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "case_results.jsonl", details)
    write_json(args.output_dir / "summary.json", summary)
    render_recall_report(summary, details, args.output_dir / "report.html")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if ragas_failures:
        return 3
    return 0 if failed_requests == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser(
        "snapshot", help="Export a private corpus snapshot over HTTP"
    )
    snapshot.add_argument(
        "--base-url",
        default=os.getenv("RAG_EVAL_BASE_URL"),
        required=not os.getenv("RAG_EVAL_BASE_URL"),
    )
    snapshot.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    snapshot.add_argument("--kb-id", action="append", required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    snapshot.add_argument("--timeout", type=float, default=60.0)
    snapshot.add_argument("--include-unready", action="store_true")
    snapshot.set_defaults(handler=snapshot_command)

    generate = subparsers.add_parser(
        "generate", help="Generate a JSONL evaluation dataset from a snapshot"
    )
    generate.add_argument("--snapshot", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--count", type=int, default=50)
    generate.add_argument("--multi-ratio", type=float, default=0.3)
    generate.add_argument("--no-hit-ratio", type=float, default=0.1)
    generate.add_argument("--seed", type=int, default=20260715)
    generate.add_argument("--max-source-chars", type=int, default=6000)
    generate.add_argument("--temperature", type=float, default=0.3)
    generate.add_argument("--timeout", type=float, default=90.0)
    generate.set_defaults(handler=generate_command)

    validate = subparsers.add_parser(
        "validate", help="Validate a generated evaluation dataset"
    )
    validate.add_argument("--dataset", type=Path, required=True)
    validate.add_argument("--snapshot", type=Path)
    validate.set_defaults(handler=validate_command)

    evaluate = subparsers.add_parser(
        "evaluate", help="Run retrieval and calculate ID/ranking metrics"
    )
    evaluate.add_argument(
        "--base-url",
        default=os.getenv("RAG_EVAL_BASE_URL"),
        required=not os.getenv("RAG_EVAL_BASE_URL"),
    )
    evaluate.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    evaluate.add_argument("--dataset", type=Path, required=True)
    evaluate.add_argument("--snapshot", type=Path)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument(
        "--retrieve-type",
        choices=["participle", "semantic", "hybrid"],
        default="hybrid",
    )
    evaluate.add_argument("--top-k", type=int, default=10)
    evaluate.add_argument("--top-n", type=int, default=20)
    evaluate.add_argument("--k", type=int, action="append")
    evaluate.add_argument("--similarity-threshold", type=float, default=0.2)
    evaluate.add_argument("--vector-similarity-weight", type=float, default=0.3)
    evaluate.add_argument("--rerank-score-threshold", type=float)
    evaluate.add_argument("--timeout", type=float, default=60.0)
    evaluate.set_defaults(handler=evaluate_command)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if getattr(args, "count", 1) < 1:
            raise EvaluationError("count must be positive")
        for name in ("multi_ratio", "no_hit_ratio"):
            value = getattr(args, name, 0.0)
            if not 0.0 <= value <= 1.0:
                raise EvaluationError(f"{name} must be between 0 and 1")
        if hasattr(args, "top_n") and args.top_n < args.top_k:
            raise EvaluationError("top_n must be greater than or equal to top_k")
        return int(args.handler(args))
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
