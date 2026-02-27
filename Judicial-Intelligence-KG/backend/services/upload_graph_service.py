from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlparse

from .groq_keyword_service import CaseMatch, GroqKeywordService
from .graph_service import GraphService
from .indiankanoon_service import ExternalSearchResult, IndianKanoonService


class UploadGraphService:
    def __init__(
        self,
        graph_service: GraphService,
        keyword_service: GroqKeywordService,
        search_service: IndianKanoonService,
    ) -> None:
        self.graph_service = graph_service
        self.keyword_service = keyword_service
        self.search_service = search_service
        self.logger = logging.getLogger("upload_graph_service")

    def ensure_constraints(self) -> None:
        statements = [
            (
                "CREATE CONSTRAINT uploaded_case_upload_id_unique IF NOT EXISTS "
                "FOR (u:UploadedCase) REQUIRE u.upload_id IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT search_keyword_norm_unique IF NOT EXISTS "
                "FOR (k:SearchKeyword) REQUIRE k.normalized IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT external_case_id_unique IF NOT EXISTS "
                "FOR (e:ExternalCase) REQUIRE e.external_case_id IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT source_document_url_unique IF NOT EXISTS "
                "FOR (s:SourceDocument) REQUIRE s.result_url IS UNIQUE"
            ),
        ]
        for statement in statements:
            self.graph_service.run_query(statement)

    def process_upload(
        self,
        *,
        filename: str,
        file_type: str,
        file_hash: str,
        extracted_text: str,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict[str, object]:
        self._emit_progress(progress_callback, 38, "ensuring_constraints")
        self.ensure_constraints()
        upload_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        self._emit_progress(progress_callback, 45, "finalizing_keywords")
        keywords = self.keyword_service.extract_keywords(extracted_text)[:6]
        self.logger.info(
            "[Upload %s] Keywords extracted (%s): %s",
            upload_id,
            len(keywords),
            keywords,
        )

        self._emit_progress(progress_callback, 52, "saving_upload_node")
        self.graph_service.run_query(
            """
            MERGE (u:UploadedCase {upload_id: $upload_id})
            SET u.filename = $filename,
                u.file_type = $file_type,
                u.sha256 = $file_hash,
                u.created_at = $created_at
            """,
            {
                "upload_id": upload_id,
                "filename": filename,
                "file_type": file_type,
                "file_hash": file_hash,
                "created_at": created_at,
            },
        )

        warnings: list[str] = []
        indexed_results = 0
        unique_results: dict[str, ExternalSearchResult] = {}
        total_keywords = max(1, len(keywords))
        for rank, keyword in enumerate(keywords, start=1):
            loop_progress = 56 + int((rank - 1) * 20 / total_keywords)
            self._emit_progress(
                progress_callback,
                loop_progress,
                f"searching_keyword_{rank}_of_{total_keywords}",
            )
            self.logger.info(
                "[Upload %s] Keyword %s/%s '%s' -> fetching up to 10 results",
                upload_id,
                rank,
                len(keywords),
                keyword,
            )
            self._upsert_keyword(upload_id=upload_id, keyword=keyword, rank=rank)
            try:
                results = self.search_service.search(keyword, limit=10)
            except Exception as exc:
                warnings.append(f"Keyword '{keyword}' failed: {exc}")
                self.logger.warning(
                    "[Upload %s] Keyword '%s' search failed: %s",
                    upload_id,
                    keyword,
                    exc,
                )
                continue

            if not results:
                warnings.append(f"No results for keyword '{keyword}'.")
                self.logger.info(
                    "[Upload %s] Keyword '%s' returned 0 results",
                    upload_id,
                    keyword,
                )
                continue

            self.logger.info(
                "[Upload %s] Keyword '%s' raw=%s",
                upload_id,
                keyword,
                len(results),
            )
            for result in results:
                if result.result_url and result.result_url not in unique_results:
                    unique_results[result.result_url] = result

        pooled_results = list(unique_results.values())
        self._emit_progress(progress_callback, 78, "cross_keyword_matching")
        self.logger.info(
            "[Upload %s] Running Groq cross-keyword filtering over pooled_results=%s",
            upload_id,
            len(pooled_results),
        )
        selected = self.keyword_service.select_cases_for_all_keywords(
            keywords=keywords,
            results=pooled_results,
            min_score=0.8,
            max_keep=30,
        )

        if not selected:
            warnings.append(
                "No cases matched all keywords at >=0.80 score; using heuristic shortlist fallback."
            )
            fallback_selected = self._shortlist_results(
                results=pooled_results,
                all_keywords=keywords,
                threshold=0.8,
                max_keep=30,
            )
            selected = [
                CaseMatch(result=item, score=0.8, matched_keywords=keywords)
                for item in fallback_selected
            ]
        else:
            self.logger.info(
                "[Upload %s] Groq strict match selected=%s",
                upload_id,
                len(selected),
            )
        self.logger.info(
            "[Upload %s] Selected case sample=%s",
            upload_id,
            [
                {
                    "url": item.result.result_url,
                    "score": round(float(item.score), 3),
                }
                for item in selected[:8]
            ],
        )

        for match in selected:
            self._emit_progress(progress_callback, 86, "saving_selected_cases")
            self._upsert_external_case_for_keywords(
                upload_id=upload_id,
                keywords=match.matched_keywords or keywords,
                result=match.result,
                score=match.score,
            )
            indexed_results += 1

        self._emit_progress(progress_callback, 95, "building_graph_view")
        graph = self.fetch_graph_for_upload(upload_id=upload_id)
        self.logger.info(
            "[Upload %s] Graph prepared nodes=%s edges=%s indexed_results=%s",
            upload_id,
            graph.get("node_count", 0),
            graph.get("edge_count", 0),
            indexed_results,
        )
        return {
            "upload_id": upload_id,
            "keywords": keywords,
            "warnings": warnings,
            "indexed_results": indexed_results,
            "graph": graph,
        }

    def _emit_progress(
        self,
        progress_callback: Callable[[int, str], None] | None,
        progress: int,
        stage: str,
    ) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(progress, stage)
        except Exception:
            self.logger.exception(
                "Progress callback failed for stage=%s progress=%s", stage, progress
            )

    def _upsert_keyword(self, *, upload_id: str, keyword: str, rank: int) -> None:
        normalized = " ".join(keyword.lower().split())
        self.graph_service.run_query(
            """
            MATCH (u:UploadedCase {upload_id: $upload_id})
            MERGE (k:SearchKeyword {normalized: $normalized})
            SET k.value = $keyword
            MERGE (u)-[r:HAS_KEYWORD]->(k)
            SET r.rank = $rank
            """,
            {
                "upload_id": upload_id,
                "normalized": normalized,
                "keyword": keyword,
                "rank": rank,
            },
        )

    def _upsert_external_case_for_keywords(
        self,
        *,
        upload_id: str,
        keywords: list[str],
        result: ExternalSearchResult,
        score: float,
    ) -> None:
        external_case_id = hashlib.sha1(result.result_url.encode("utf-8")).hexdigest()
        host = urlparse(result.result_url).netloc.lower()
        safe_keywords = [k for k in keywords if k and k.strip()]
        if not safe_keywords:
            return

        for keyword in safe_keywords:
            normalized = " ".join(keyword.lower().split())
            self.graph_service.run_query(
                """
                MATCH (u:UploadedCase {upload_id: $upload_id})
                MATCH (k:SearchKeyword {normalized: $keyword_norm})
                MERGE (e:ExternalCase {external_case_id: $external_case_id})
                SET e.title = $title,
                    e.court = $court,
                    e.date = $date,
                    e.source = "indiakanoon",
                    e.snippet = $snippet
                MERGE (k)-[rk:RETURNED_CASE]->(e)
                SET rk.rank = $rank,
                    rk.query_text = $keyword_value,
                    rk.relevance_score = $score
                MERGE (u)-[:RELATED_TO_CASE]->(e)
                MERGE (s:SourceDocument {result_url: $result_url})
                SET s.document_url = $document_url,
                    s.host = $host
                MERGE (e)-[:HAS_SOURCE]->(s)
                """,
                {
                    "upload_id": upload_id,
                    "keyword_norm": normalized,
                    "keyword_value": keyword,
                    "external_case_id": external_case_id,
                    "title": result.title,
                    "court": result.court,
                    "date": result.date,
                    "snippet": result.snippet,
                    "rank": result.rank,
                    "score": float(score),
                    "result_url": result.result_url,
                    "document_url": result.document_url,
                    "host": host,
                },
            )

    def fetch_graph_for_upload(self, *, upload_id: str) -> dict[str, object]:
        rows = self.graph_service.run_query(
            """
            MATCH (u:UploadedCase {upload_id: $upload_id})
            OPTIONAL MATCH p=(u)-[*1..3]-(n)
            WITH collect(DISTINCT nodes(p)) AS node_groups, collect(DISTINCT relationships(p)) AS rel_groups, u
            WITH
              reduce(node_acc = [u], ng IN node_groups | node_acc + ng) AS raw_nodes,
              reduce(rel_acc = [], rg IN rel_groups | rel_acc + rg) AS raw_rels,
              elementId(u) AS upload_node_id
            RETURN
              [node IN raw_nodes WHERE node IS NOT NULL | {
                id: elementId(node),
                labels: labels(node),
                properties: properties(node),
                score: CASE WHEN elementId(node) = upload_node_id THEN 100 ELSE null END
              }] AS nodes,
              [rel IN raw_rels WHERE rel IS NOT NULL | {
                id: elementId(rel),
                type: type(rel),
                source: elementId(startNode(rel)),
                target: elementId(endNode(rel)),
                weight: CASE
                  WHEN type(rel) = "RETURNED_CASE" THEN 1.4
                  ELSE 1.0
                END
              }] AS edges
            """,
            {"upload_id": upload_id},
        )

        if not rows:
            return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0, "upload_id": upload_id}

        row = rows[0]
        dedup_nodes = self._dedupe_by_id(row.get("nodes", []) or [])
        dedup_edges = self._dedupe_by_id(row.get("edges", []) or [])
        return {
            "nodes": dedup_nodes,
            "edges": dedup_edges,
            "node_count": len(dedup_nodes),
            "edge_count": len(dedup_edges),
            "upload_id": upload_id,
        }

    def fetch_upload_details(self, *, upload_id: str) -> dict[str, object]:
        rows = self.graph_service.run_query(
            """
            MATCH (u:UploadedCase {upload_id: $upload_id})
            OPTIONAL MATCH (u)-[hk:HAS_KEYWORD]->(k:SearchKeyword)
            OPTIONAL MATCH (u)-[:RELATED_TO_CASE]->(e:ExternalCase)-[:HAS_SOURCE]->(s:SourceDocument)
            WITH u,
                 collect(DISTINCT {value: k.value, rank: hk.rank}) AS keyword_rows,
                 collect(DISTINCT {
                    title: e.title,
                    court: e.court,
                    date: e.date,
                    snippet: e.snippet,
                    result_url: s.result_url,
                    document_url: s.document_url
                 }) AS external_rows
            RETURN
              u.upload_id AS upload_id,
              u.filename AS filename,
              u.file_type AS file_type,
              u.sha256 AS sha256,
              u.created_at AS created_at,
              [x IN keyword_rows WHERE x.value IS NOT NULL] AS keywords,
              [x IN external_rows WHERE x.title IS NOT NULL OR x.result_url IS NOT NULL] AS related_cases
            """,
            {"upload_id": upload_id},
        )
        return rows[0] if rows else {}

    def _dedupe_by_id(self, items: list[dict]) -> list[dict]:
        seen: dict[str, dict] = {}
        for item in items:
            item_id = str(item.get("id"))
            if item_id not in seen:
                seen[item_id] = item
        return list(seen.values())

    def _shortlist_results(
        self,
        *,
        results: list[ExternalSearchResult],
        all_keywords: list[str],
        threshold: float,
        max_keep: int,
    ) -> list[ExternalSearchResult]:
        keyword_tokens = [self._keyword_token_set(k) for k in all_keywords if k.strip()]
        if not keyword_tokens:
            return results[:max_keep]

        scored: list[tuple[float, ExternalSearchResult]] = []
        for result in results:
            haystack = f"{result.title} {result.snippet}".lower()
            matches = 0
            for token_set in keyword_tokens:
                if not token_set:
                    continue
                if all(token in haystack for token in token_set):
                    matches += 1
            ratio = matches / max(1, len(keyword_tokens))
            scored.append((ratio, result))

        strict = [item for item in scored if item[0] >= threshold]
        strict.sort(key=lambda x: x[0], reverse=True)
        selected = [item[1] for item in strict[:max_keep]]

        if len(selected) < max_keep:
            remaining = [item for item in scored if item[1] not in selected]
            remaining.sort(key=lambda x: x[0], reverse=True)
            for _, result in remaining:
                selected.append(result)
                if len(selected) >= max_keep:
                    break

        return selected[:max_keep]

    def _keyword_token_set(self, keyword: str) -> set[str]:
        tokens = re.findall(r"[a-z0-9]{3,}", keyword.lower())
        return {t for t in tokens if t}
