from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover - optional at runtime
    genai = None


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


class GeminiKeywordService:
    def __init__(self) -> None:
        self.logger = logging.getLogger("gemini_keyword_service")
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()
        self.keyword_count = int(os.getenv("GEMINI_KEYWORD_COUNT", "6"))
        self.allow_fallback = os.getenv("GEMINI_ALLOW_FALLBACK", "1").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self._resolved_model_name: str | None = None

    def extract_keywords(self, text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            raise ValueError("No text found in file.")

        if self.api_key and genai is not None:
            try:
                self.logger.warning(
                    "GEMINI API CALL start model=%s requested_keywords=%s",
                    self.model_name,
                    self.keyword_count,
                )
                keywords = self._extract_with_gemini(text)
                self.logger.warning("GEMINI API CALL success keywords=%s", keywords)
                return keywords
            except Exception as exc:
                self.logger.exception("GEMINI API CALL failed: %s", exc)
                if not self.allow_fallback:
                    raise RuntimeError(
                        f"Gemini keyword extraction failed and fallback is disabled. Root cause: {exc}"
                    ) from exc
                self.logger.warning("Using fallback keyword extractor after Gemini failure.")
                return self._fallback_keywords(text)

        if self.api_key and genai is None:
            raise RuntimeError(
                "GEMINI_API_KEY is set but google.generativeai package is unavailable."
            )

        if not self.allow_fallback:
            raise RuntimeError(
                "GEMINI_API_KEY missing and fallback disabled (set GEMINI_ALLOW_FALLBACK=1 to allow fallback)."
            )

        return self._fallback_keywords(text)

    def _extract_with_gemini(self, text: str) -> list[str]:
        genai.configure(api_key=self.api_key)
        model_name = self._resolve_model_name()
        model = genai.GenerativeModel(model_name)
        clipped = text[:14000]
        prompt = (
            "You are extracting legal search intents for broad case discovery.\n"
            "Return ONLY valid JSON with this exact schema:\n"
            '{"keywords":["k1","k2","k3","k4","k5","k6"]}\n'
            "Rules:\n"
            f"1) Exactly {self.keyword_count} generalized legal-topic keywords.\n"
            "2) Avoid party/company/person names and specific branded entities.\n"
            "3) Prefer broad discoverable legal concepts (2-3 words when possible).\n"
            "4) Lowercase only.\n"
            "5) No punctuation-heavy strings.\n\n"
            f"Case text:\n{clipped}"
        )
        response = model.generate_content(prompt)
        raw = (getattr(response, "text", "") or "").strip()
        try:
            return self._parse_and_normalize(raw, fallback_text=text)
        except Exception:
            # Strict retry with tighter instruction before failing (still no fallback mode).
            retry_prompt = (
                "Return STRICT JSON only.\n"
                '{"keywords":["k1","k2","k3","k4","k5","k6"]}\n'
                f"Exactly {self.keyword_count} generalized legal keywords.\n"
                "Do not include names of people/companies.\n"
                f"Text:\n{clipped}"
            )
            retry_response = model.generate_content(retry_prompt)
            retry_raw = (getattr(retry_response, "text", "") or "").strip()
            return self._parse_and_normalize(retry_raw, fallback_text=text)

    def _resolve_model_name(self) -> str:
        if self._resolved_model_name:
            return self._resolved_model_name

        preferred = [self.model_name]
        fallbacks = [
            "gemini-1.5-flash-latest",
            "gemini-1.5-flash-001",
            "gemini-1.5-pro-latest",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        ]
        candidates = []
        for name in preferred + fallbacks:
            if name and name not in candidates:
                candidates.append(name)

        # Try to discover supported models for this API key + SDK version.
        available: set[str] = set()
        try:
            for model in genai.list_models():
                model_name = str(getattr(model, "name", "") or "")
                if not model_name:
                    continue
                methods = set(getattr(model, "supported_generation_methods", []) or [])
                if "generateContent" in methods:
                    # names are usually in form "models/<name>"
                    available.add(model_name.replace("models/", ""))
        except Exception as exc:
            self.logger.warning("GEMINI model discovery failed: %s", exc)

        if available:
            for candidate in candidates:
                if candidate in available:
                    self._resolved_model_name = candidate
                    self.logger.warning("GEMINI model resolved to %s", candidate)
                    return candidate
            # If configured model not present, pick a flash-like available model first.
            flash_like = [name for name in sorted(available) if "flash" in name]
            chosen = flash_like[0] if flash_like else sorted(available)[0]
            self._resolved_model_name = chosen
            self.logger.warning("GEMINI model auto-selected %s", chosen)
            return chosen

        # If discovery unavailable, try configured model first.
        self._resolved_model_name = self.model_name
        return self._resolved_model_name

    def _parse_and_normalize(self, payload: str, fallback_text: str) -> list[str]:
        start = payload.find("{")
        end = payload.rfind("}")
        if start == -1 or end == -1 or end <= start:
            if self.allow_fallback:
                return self._fallback_keywords(fallback_text)
            raise ValueError("Gemini payload did not contain JSON object.")

        try:
            obj = json.loads(payload[start : end + 1])
        except json.JSONDecodeError:
            if self.allow_fallback:
                return self._fallback_keywords(fallback_text)
            raise ValueError("Gemini payload JSON parsing failed.")

        keywords = obj.get("keywords", [])
        if not isinstance(keywords, list):
            if self.allow_fallback:
                return self._fallback_keywords(fallback_text)
            raise ValueError("Gemini response missing keyword list.")

        normalized = self._normalize_keywords(keywords)
        if len(normalized) < self.keyword_count:
            if not self.allow_fallback:
                raise ValueError(
                    f"Gemini returned insufficient usable keywords: {len(normalized)}"
                )
            for kw in self._fallback_keywords(fallback_text):
                if kw not in normalized:
                    normalized.append(kw)
                if len(normalized) >= self.keyword_count:
                    break

        return normalized[: self.keyword_count]

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

    def _fallback_keywords(self, text: str) -> list[str]:
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", text.lower())
        filtered = [t for t in tokens if t not in STOPWORDS and not t.isdigit()]
        counts = Counter(filtered)
        ranked = [word for word, _ in counts.most_common(120)]

        phrase_counts: Counter[str] = Counter()
        for idx in range(len(filtered) - 1):
            a, b = filtered[idx], filtered[idx + 1]
            if a in STOPWORDS or b in STOPWORDS:
                continue
            if len(a) >= 4 and len(b) >= 4:
                phrase_counts[f"{a} {b}"] += 1

        chosen: list[str] = []
        for phrase, _ in phrase_counts.most_common(30):
            g = self._generalize_phrase(phrase)
            if g and g not in chosen:
                chosen.append(g)
            if len(chosen) >= self.keyword_count:
                break

        for word in ranked:
            if len(chosen) >= self.keyword_count:
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
        ]
        for default_kw in defaults:
            if len(chosen) >= self.keyword_count:
                break
            if default_kw not in chosen:
                chosen.append(default_kw)

        return chosen[: self.keyword_count]
