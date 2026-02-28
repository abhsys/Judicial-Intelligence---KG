from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass

import httpx

try:
    from .indiankanoon_service import ExternalSearchResult
except ImportError:
    from indiankanoon_service import ExternalSearchResult


STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "from",
    "are",
    "was",
    "were",
    "have",
    "has",
    "had",
    "not",
    "but",
    "into",
    "about",
    "case",
    "court",
    "order",
    "petitioner",
    "respondent",
    "law",
    "legal",
    "under",
    "shall",
    "would",
    "which",
    "their",
    "there",
    "where",
    "when",
    "his",
    "her",
    "hers",
    "him",
    "she",
    "he",
    "they",
    "them",
    "its",
    "our",
    "your",
    "you",
    "any",
    "all",
    "been",
    "being",
    "also",
    "such",
    "than",
    "then",
    "upon",
    "hereby",
    "thereof",
    "therein",
    "thereby",
    "plaintiff",
    "defendant",
    "appellant",
    "appellee",
    "honble",
    "judge",
    "justice",
    "section",
    "article",
    "act",
    "state",
    "india",
    "mr",
    "mrs",
    "ms",
    "dr",
    "m/s",
    "ltd",
    "limited",
    "llp",
    "inc",
    "corp",
    "co",
    "company",
    "private",
    "pvt",
    "vs",
    "versus",
    "v",
    "others",
    "another",
    "anr",
    "ors",
    "indiankanoon",
    "kanoon",
}

LEGAL_CORE = {
    "contract",
    "dispute",
    "criminal",
    "civil",
    "property",
    "land",
    "tenancy",
    "rental",
    "insurance",
    "accident",
    "liability",
    "negligence",
    "employment",
    "service",
    "tax",
    "arbitration",
    "fraud",
    "cheque",
    "evidence",
    "procedure",
    "bail",
    "custody",
    "maintenance",
    "motor",
    "motors",
    "vehicle",
    "vehicles",
    "consumer",
    "compensation",
    "injury",
    "compliance",
    "appeal",
    "jurisdiction",
    "constitutional",
}

TOKEN_REPLACEMENTS = {
    "automobile": "motor",
    "automobiles": "motor",
    "automotive": "motor",
    "cars": "motor",
    "car": "motor",
    "vehicles": "motor",
    "vehicular": "motor",
    "employment": "service",
    "worker": "service",
    "workers": "service",
    "tenant": "tenancy",
    "tenants": "tenancy",
    "landlord": "tenancy",
    "landlords": "tenancy",
    "compensatory": "compensation",
}


@dataclass
class CaseMatch:
    result: ExternalSearchResult
    score: float
    matched_keywords: list[str]


class GroqKeywordService:
    def __init__(self) -> None:
        self.logger = logging.getLogger("groq_keyword_service")
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.model_name = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
        self.keyword_count = int(os.getenv("GROQ_KEYWORD_COUNT", "6"))
        self.allow_fallback = os.getenv("GROQ_ALLOW_FALLBACK", "1").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self.timeout_seconds = float(os.getenv("GROQ_TIMEOUT_SECONDS", "40"))
        self.api_base = (
            os.getenv("GROQ_API_BASE", "https://api.groq.com/openai/v1")
            .strip()
            .rstrip("/")
        )

    def extract_keywords(self, text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            raise ValueError("No text found in file.")

        local_candidates = self._fallback_keywords(text, desired=max(18, self.keyword_count * 3))
        self.logger.info(
            "GROQ keyword pipeline start text_chars=%s local_candidates=%s model=%s",
            len(text),
            len(local_candidates),
            self.model_name,
        )
        if self.api_key:
            try:
                finalized = self._finalize_keywords_with_groq(text, local_candidates)
                if len(finalized) >= self.keyword_count:
                    self.logger.info(
                        "GROQ keyword pipeline success finalized_keywords=%s",
                        finalized[: self.keyword_count],
                    )
                    return finalized[: self.keyword_count]
            except Exception as exc:
                self.logger.exception("GROQ keyword finalization failed: %s", exc)
                if not self.allow_fallback:
                    raise

        self.logger.warning(
            "GROQ keyword pipeline fallback used allow_fallback=%s api_key_present=%s",
            self.allow_fallback,
            bool(self.api_key),
        )
        if not self.allow_fallback and not self.api_key:
            raise RuntimeError(
                "GROQ_API_KEY missing and fallback disabled (set GROQ_ALLOW_FALLBACK=1 to allow fallback)."
            )
        return local_candidates[: self.keyword_count]

    def select_cases_for_all_keywords(
        self,
        *,
        keywords: list[str],
        results: list[ExternalSearchResult],
        min_score: float = 0.8,
        max_keep: int = 30,
    ) -> list[CaseMatch]:
        clean_keywords = [k.strip().lower() for k in keywords if k and k.strip()]
        if not clean_keywords or not results:
            return []
        self.logger.info(
            "GROQ case-match start keywords=%s candidates=%s min_score=%.2f max_keep=%s",
            clean_keywords,
            len(results),
            min_score,
            max_keep,
        )

        if not self.api_key:
            self.logger.warning("GROQ case-match running heuristic fallback: api_key missing")
            return self._fallback_case_match(clean_keywords, results, min_score=min_score, max_keep=max_keep)

        compact_rows = []
        for idx, item in enumerate(results, start=1):
            compact_rows.append(
                {
                    "index": idx,
                    "result_url": item.result_url,
                    "title": (item.title or "")[:220],
                    "snippet": (item.snippet or "")[:260],
                    "court": item.court,
                    "date": item.date,
                }
            )

        prompt = (
            "Given legal keywords and candidate cases, identify cases that are jointly relevant to ALL keywords.\n"
            "Return strict JSON only with this schema:\n"
            '{"cases":[{"result_url":"...","score":0.0,"matched_keywords":["k1","k2","k3","k4","k5","k6"]}]}\n'
            "Rules:\n"
            "1) score must be 0.0 to 1.0.\n"
            "2) Include ONLY cases where score >= 0.80 and matched_keywords contains all provided keywords.\n"
            "3) matched_keywords values must be from the exact provided keyword list.\n"
            "4) Keep at most 30 best cases.\n\n"
            f"keywords: {json.dumps(clean_keywords, ensure_ascii=True)}\n"
            f"candidates: {json.dumps(compact_rows, ensure_ascii=True)}"
        )
        payload = {
            "model": self.model_name,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
        }
        data = self._chat_completion(payload)
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        selected = self._parse_case_matches(
            payload=content,
            clean_keywords=clean_keywords,
            results=results,
            min_score=min_score,
            max_keep=max_keep,
        )
        if selected:
            self.logger.info(
                "GROQ case-match success selected=%s top_scores=%s",
                len(selected),
                [round(item.score, 3) for item in selected[:5]],
            )
            return selected
        self.logger.warning("GROQ case-match returned no strict matches; using heuristic fallback")
        return self._fallback_case_match(clean_keywords, results, min_score=min_score, max_keep=max_keep)

    def _finalize_keywords_with_groq(self, text: str, candidates: list[str]) -> list[str]:
        clipped = text[:14000]
        self.logger.info(
            "GROQ keyword finalize request model=%s candidate_sample=%s",
            self.model_name,
            candidates[:10],
        )
        prompt = (
            "You are finalizing legal search keywords.\n"
            "Input contains locally extracted candidates from a Python script.\n"
            "Return STRICT JSON only with schema:\n"
            '{"keywords":["k1","k2","k3","k4","k5","k6"]}\n'
            "Rules:\n"
            f"1) Exactly {self.keyword_count} keywords.\n"
            "2) Lowercase.\n"
            "3) 2-3 words preferred.\n"
            "4) Avoid person/company/case names.\n"
            "5) Keep broad legal intent useful for case search.\n\n"
            f"candidate_keywords: {json.dumps(candidates[:30], ensure_ascii=True)}\n"
            f"case_text: {clipped}"
        )
        payload = {
            "model": self.model_name,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
        }
        data = self._chat_completion(payload)
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        parsed = self._parse_keyword_payload(content)
        normalized = self._normalize_keywords(parsed)
        self.logger.info(
            "GROQ keyword finalize raw_count=%s normalized_count=%s",
            len(parsed),
            len(normalized),
        )

        for candidate in candidates:
            if len(normalized) >= self.keyword_count:
                break
            item = self._normalize_keywords([candidate])
            if item and item[0] not in normalized:
                normalized.append(item[0])

        return normalized[: self.keyword_count]

    def _chat_completion(self, payload: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is missing.")
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        current_payload = json.loads(json.dumps(payload))
        max_attempts = 4
        attempt = 1
        while True:
            self.logger.info(
                "GROQ API CALL start endpoint=%s model=%s attempt=%s prompt_chars=%s",
                url,
                current_payload.get("model"),
                attempt,
                self._user_prompt_chars(current_payload),
            )
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(url, headers=headers, json=current_payload)
                    response.raise_for_status()
                    data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 413 and attempt < max_attempts:
                    shrunk = self._shrink_user_prompt(current_payload)
                    if shrunk:
                        self.logger.warning(
                            "GROQ API CALL 413 Payload Too Large; shrinking prompt and retrying (attempt=%s)",
                            attempt + 1,
                        )
                        attempt += 1
                        continue
                raise

        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        self.logger.info(
            "GROQ API CALL success status=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s",
            response.status_code,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )
        return data

    def _shrink_user_prompt(self, payload: dict) -> bool:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return False
        user_indices = [
            idx
            for idx, msg in enumerate(messages)
            if isinstance(msg, dict) and msg.get("role") == "user" and isinstance(msg.get("content"), str)
        ]
        if not user_indices:
            return False

        idx = user_indices[-1]
        content = messages[idx]["content"]
        old_len = len(content)
        if old_len <= 1800:
            return False
        new_len = max(1200, int(old_len * 0.6))
        if new_len >= old_len:
            return False
        messages[idx]["content"] = content[:new_len]
        return True

    def _user_prompt_chars(self, payload: dict) -> int:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return 0
        total = 0
        for msg in messages:
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                total += len(msg["content"])
        return total

    def _parse_keyword_payload(self, payload: str) -> list[str]:
        start = payload.find("{")
        end = payload.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("Groq keyword payload did not contain JSON object.")
        obj = json.loads(payload[start : end + 1])
        keywords = obj.get("keywords", [])
        if not isinstance(keywords, list):
            raise ValueError("Groq keyword response missing keyword list.")
        return [str(item) for item in keywords]

    def _parse_case_matches(
        self,
        *,
        payload: str,
        clean_keywords: list[str],
        results: list[ExternalSearchResult],
        min_score: float,
        max_keep: int,
    ) -> list[CaseMatch]:
        start = payload.find("{")
        end = payload.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []

        try:
            obj = json.loads(payload[start : end + 1])
        except json.JSONDecodeError:
            return []

        rows = obj.get("cases", [])
        if not isinstance(rows, list):
            return []

        by_url = {item.result_url: item for item in results if item.result_url}
        required_set = set(clean_keywords)
        parsed: list[CaseMatch] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = str(row.get("result_url", "")).strip()
            result = by_url.get(url)
            if not result:
                continue
            try:
                score = float(row.get("score", 0))
            except (TypeError, ValueError):
                score = 0.0

            matched = row.get("matched_keywords", [])
            if not isinstance(matched, list):
                matched = []
            matched_clean = [str(item).strip().lower() for item in matched if str(item).strip()]
            matched_set = set(matched_clean)

            if score < min_score:
                continue
            if not required_set.issubset(matched_set):
                continue

            parsed.append(CaseMatch(result=result, score=score, matched_keywords=clean_keywords))

        parsed.sort(key=lambda item: item.score, reverse=True)
        self.logger.info(
            "GROQ case-match parsed_rows=%s accepted_rows=%s",
            len(rows),
            len(parsed),
        )
        return parsed[:max_keep]

    def _fallback_case_match(
        self,
        keywords: list[str],
        results: list[ExternalSearchResult],
        *,
        min_score: float,
        max_keep: int,
    ) -> list[CaseMatch]:
        keyword_tokens = [self._keyword_token_set(k) for k in keywords if k.strip()]
        if not keyword_tokens:
            return []

        selected: list[CaseMatch] = []
        for result in results:
            haystack = f"{result.title} {result.snippet}".lower()
            matched = 0
            for token_set in keyword_tokens:
                if token_set and all(token in haystack for token in token_set):
                    matched += 1
            coverage = matched / max(1, len(keyword_tokens))
            if coverage >= min_score:
                selected.append(
                    CaseMatch(
                        result=result,
                        score=coverage,
                        matched_keywords=keywords,
                    )
                )

        selected.sort(key=lambda item: item.score, reverse=True)
        self.logger.info(
            "Heuristic case-match selected=%s from=%s min_score=%.2f",
            len(selected),
            len(results),
            min_score,
        )
        return selected[:max_keep]

    def _keyword_token_set(self, keyword: str) -> set[str]:
        tokens = re.findall(r"[a-z0-9]{3,}", keyword.lower())
        return {t for t in tokens if t}

    def _normalize_keywords(self, keywords: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in keywords:
            val = re.sub(r"[^a-z0-9\s-]", " ", str(item).lower())
            val = " ".join(val.split())
            if len(val) < 3:
                continue
            parts = [p for p in val.split() if p]
            if not parts:
                continue
            if len(parts) == 1 and parts[0] in STOPWORDS:
                continue
            meaningful = [p for p in parts if p not in STOPWORDS and len(p) > 2]
            if not meaningful:
                continue
            if len(parts) == 1 and len(meaningful[0]) < 5:
                continue

            generalized = self._generalize_phrase(" ".join(parts))
            if not generalized:
                continue
            if generalized not in cleaned:
                cleaned.append(generalized)
        return cleaned

    def _generalize_phrase(self, phrase: str) -> str:
        parts = [p for p in phrase.split() if p and p not in STOPWORDS and not p.isdigit()]
        if not parts:
            return ""

        parts = [TOKEN_REPLACEMENTS.get(p, p) for p in parts]
        if len(parts) >= 2 and parts[0] not in LEGAL_CORE:
            parts = parts[1:]
        if len(parts) > 3:
            core = [p for p in parts if p in LEGAL_CORE]
            parts = core[:3] if core else parts[-3:]

        parts = [p for p in parts if p not in STOPWORDS and len(p) >= 4]
        if not parts:
            return ""
        if len(parts) == 1 and parts[0] not in LEGAL_CORE and len(parts[0]) < 6:
            return ""
        return " ".join(parts)

    def _fallback_keywords(self, text: str, desired: int) -> list[str]:
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", text.lower())
        filtered = [t for t in tokens if t not in STOPWORDS and not t.isdigit()]
        counts = Counter(filtered)
        ranked = [word for word, _ in counts.most_common(140)]

        phrase_counts: Counter[str] = Counter()
        for idx in range(len(filtered) - 1):
            a, b = filtered[idx], filtered[idx + 1]
            if a in STOPWORDS or b in STOPWORDS:
                continue
            if len(a) >= 4 and len(b) >= 4:
                phrase_counts[f"{a} {b}"] += 1

        chosen: list[str] = []
        for phrase, _ in phrase_counts.most_common(60):
            g = self._generalize_phrase(phrase)
            if g and g not in chosen:
                chosen.append(g)
            if len(chosen) >= desired:
                break

        for word in ranked:
            if len(chosen) >= desired:
                break
            if word in STOPWORDS or len(word) < 5:
                continue
            g = self._generalize_phrase(word)
            if g and g not in chosen:
                chosen.append(g)

        defaults = [
            "contract dispute",
            "property dispute",
            "criminal procedure",
            "consumer protection",
            "civil liability",
            "procedural compliance",
            "jurisdiction dispute",
            "evidence procedure",
        ]
        for default_kw in defaults:
            if len(chosen) >= desired:
                break
            if default_kw not in chosen:
                chosen.append(default_kw)

        return chosen[:desired]
