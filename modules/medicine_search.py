"""Google-like medicine lookup for MediScan AI.

The demo CSV in ``data/medicines.csv`` only holds a few dozen entries, so the
scanner could not answer for most real-world names (Indian brand names,
prescription drugs, newer medicines).  This module fills that gap by asking
free, key-less public drug databases, exactly like a general web search would,
and merging whatever comes back into one result:

* openFDA drug labels - brand/generic name, purpose, indications, warnings,
  side effects, interactions, precautions, form, route, manufacturer.
* RxNorm (NIH)         - normalised name, ingredient, and other names.
* Wikipedia            - plain-language summary, page link, and image.

Every source is optional, and the three lookups run concurrently under a
per-stage deadline: if one is slow, rate-limited, or unreachable the answer is
still built from whatever the other sources found, so a flaky network degrades
the result instead of breaking the feature.

Because these databases are searched by name, a plain substring hit is not
enough: "dolo" also matches a Wikipedia page about methadone.  Every candidate
is therefore relevance-checked and scored against the words the user actually
typed before it is allowed to contribute to the answer.

Safety note: the label text contains specific dosing instructions.  MediScan's
assistant policy is to never show a personal dosage amount, so
``dosage_and_administration`` is deliberately never surfaced - only the
"before food / after food" style timing that already exists in the demo CSV is
shown to users.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any
from urllib.parse import quote

try:  # requests ships with Streamlit, but keep the import failure non-fatal.
    import requests
except ImportError:  # pragma: no cover - exercised only without requests
    requests = None


USER_AGENT = "MediScanAI/1.0 (educational health prototype)"

OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
RXNORM_URL = "https://rxnav.nlm.nih.gov/REST"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"

# Per-request and per-stage budgets.  A medicine search should feel snappy, so
# a source that misses the deadline is dropped rather than blocking the page.
DEFAULT_TIMEOUT = 3.0
STAGE_DEADLINE = 5.5
# The second Wikipedia attempt (using the generic name the drug databases
# resolved the brand to) gets its own budget: it is the last thing to run, so
# it should not be starved by a slow openFDA response.
WIKI_RETRY_DEADLINE = 3.0

# Dose strengths and dosage-form words that add nothing to a database search.
_NOISE_TERMS = re.compile(
    r"\b("
    r"\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|%|iu)?|"
    r"tab(?:let)?s?|cap(?:sule)?s?|sachets?|syrup|suspension|injection|inj|"
    r"ointment|cream|gel|drops?|spray|inhaler|powder|strip|bottle|"
    r"xl|xr|sr|er|dr|mr|cd|wd|act|ds|plus|"
    r"before\s*food|after\s*food|empty\s*stomach"
    r")\b",
    re.IGNORECASE,
)

# Words that carry no identifying meaning when checking "is this the same drug?".
_STOPWORDS = {
    "mg", "ml", "mcg", "iu", "the", "and", "for", "tab", "tabs", "tablet",
    "tablets", "cap", "caps", "capsule", "capsules", "syrup", "cream", "gel",
    "drops", "spray", "powder", "strip", "inhaler", "inj", "injection",
    "suspension", "ointment", "bottle", "sachet", "sachets", "xl", "xr", "sr",
    "er", "dr", "mr", "cd", "wd", "ds", "act", "plus", "oral", "tablet",
    "max", "min", "fast", "quick",
}

# RxNorm term types worth showing to a user.
_RXNORM_TTY_LABELS = {
    "IN": "Ingredient",
    "PIN": "Precise ingredient",
    "SCD": "Clinical drug",
    "SBD": "Branded drug",
    "GPCK": "Generic pack",
    "BPCK": "Branded pack",
}
_RXNORM_RELATED_TTY = ("SCD", "SBD")

# How much a name being found in each openFDA field is trusted, and how close
# the name has to be to the query to be considered a good match.
_OPENFDA_MIN_SCORE = 2.0
_BRAND_WEIGHT = 3.0
_GENERIC_WEIGHT = 2.5
_SUBSTANCE_WEIGHT = 2.0
# How many label records to pull per field.  Every record is a full label, so
# this is the main driver of how long a lookup takes.
OPENFDA_LIMIT = 4

# Short names are ambiguous on Wikipedia ("Dolo" is also a town and a
# politician), so an article is only accepted when it reads like a medicine.
_MEDICINE_CONTEXT_WORDS = (
    "drug", "medicine", "medication", "pharmaceutical", "analgesic",
    "antibiotic", "antihistamine", "antidepressant", "tablet", "capsule",
    "injection", "ointment", "syrup", "dose", "dosage", "therapy",
    "treatment", "symptom", "disease", "infection", "pain", "fever",
    "diabetes", "blood pressure", "inflammation", "prescription", "patient",
    "fda", "generic name", "side effect", "contraindicated", "placebo",
)


# ============================================================
# Small text helpers
# ============================================================

def normalize_query(name: str) -> str:
    """Trim a typed medicine name down to its searchable core."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    text = re.sub(r"[()\[\]]", " ", text)
    cleaned = _NOISE_TERMS.sub(" ", text)
    cleaned = re.sub(r"[^\w\s+./-]", " ", cleaned, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -+./")
    return cleaned or text


def search_terms(name: str) -> list[str]:
    """Return the query variants to try, most specific first."""
    raw = re.sub(r"\s+", " ", str(name or "")).strip()
    terms = [raw] if raw else []
    cleaned = normalize_query(raw)
    if cleaned and cleaned.lower() != raw.lower():
        terms.append(cleaned)
    return terms


def relevance_tokens(term: str) -> list[str]:
    """Words that must appear in a result for it to be about the same drug."""
    tokens = []
    for token in re.split(r"[\W_]+", str(term or "").lower()):
        if len(token) > 2 and token.isalpha() and token not in _STOPWORDS:
            tokens.append(token)
    return tokens


def _word_match(tokens: list[str], text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered.strip():
        return False
    return all(re.search(rf"\b{re.escape(token)}\b", lowered) for token in tokens)


def is_relevant(term: str, *texts: Any) -> bool:
    """True when every meaningful word of ``term`` appears in ``texts``.

    Whole-word matching matters here: a plain substring test would accept
    "dolo" for an article about methadone ("Dolophine").
    """
    tokens = relevance_tokens(term)
    if not tokens:
        return False
    return _word_match(tokens, " ".join(str(text or "") for text in texts))


def name_score(term: str, name: str, weight: float) -> float:
    """Score how well ``name`` answers ``term`` (0 means "not this drug")."""
    tokens = relevance_tokens(term)
    if not tokens or not _word_match(tokens, name):
        return 0.0

    score = weight
    cleaned = normalize_query(name).lower()
    query = normalize_query(term).lower()
    if cleaned and cleaned == query:
        score += 4.0
    elif cleaned.startswith(query):
        score += 2.0
    # A long name that merely *contains* the word gets no bonus on purpose:
    # "Sitagliptin And Metformin Hydrochloride" is a different medicine to
    # someone who searched for "Metformin".
    # A short, exact name is more likely to be the drug the user meant, so a
    # name padded with extra words is discounted.
    extra_words = len(re.findall(r"[a-z]+", cleaned)) - len(tokens)
    score -= min(max(extra_words, 0), 4) * 0.25
    return score


def condense(text: Any, max_chars: int = 620) -> str:
    """Flatten an openFDA label block into a short, readable summary.

    Label sections are one long string with bullet separators ("x • y • z").
    Splitting on the bullets keeps the most useful facts and drops the rest
    instead of showing a wall of text.
    """
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned

    pieces = [p.strip(" •·-\t") for p in re.split(r"[•·]", cleaned)]
    pieces = [p for p in pieces if len(p) > 2]
    out = ""
    for piece in pieces:
        candidate = f"{out} • {piece}" if out else piece
        if out and len(candidate) > max_chars:
            break
        out = candidate
    if not out:
        out = cleaned[:max_chars].rsplit(" ", 1)[0]
    if out and out[-1] not in ".!?":
        out = out.rstrip(",;:- ") + "…"
    return out


def _first(values: Any) -> str:
    if isinstance(values, (list, tuple)):
        for value in values:
            if value:
                return str(value).strip()
        return ""
    return str(values).strip() if values else ""


def _dedupe(values: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            ordered.append(text)
    return ordered


def title_case(text: str) -> str:
    """Normalise shouted label text ("ACETAMINOPHEN" -> "Acetaminophen")."""
    value = str(text or "").strip()
    if not value or not value.isupper():
        return value
    return value.title()


# ============================================================
# HTTP plumbing
# ============================================================

def _get_json(
    url: str, params: dict[str, Any] | None = None, timeout: float = DEFAULT_TIMEOUT
):
    """GET a JSON document, returning None on any failure."""
    if requests is None:
        return None
    try:
        response = requests.get(
            url,
            params=params,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        if response.status_code != 200:
            return None
        return response.json()
    except Exception:
        # A lookup is a convenience: any network/parse problem simply means
        # this source contributes nothing.
        return None


def _gather(jobs: list, deadline: float) -> list:
    """Run callables concurrently and return their results, in job order.

    Sources that miss the deadline are reported as ``None`` instead of
    holding up the page, so the answer is built from whatever arrived in time.
    """
    results: list = [None] * len(jobs)
    if not jobs:
        return results

    pool = ThreadPoolExecutor(max_workers=len(jobs))
    futures = [pool.submit(_safe_call, job) for job in jobs]
    started = time.monotonic()
    try:
        for index, future in enumerate(futures):
            remaining = deadline - (time.monotonic() - started)
            try:
                results[index] = future.result(timeout=max(remaining, 0.0))
            except Exception:
                results[index] = None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def _safe_call(job):
    try:
        return job()
    except Exception:
        return None


def _await(future, deadline: float):
    """Wait for one future, giving up (as ``None``) when the deadline passes."""
    try:
        return future.result(timeout=max(deadline, 0.0))
    except Exception:
        return None



# ============================================================
# Source 1 — openFDA drug labels
# ============================================================

def _openfda_search(query: str, limit: int = 5, timeout: float = DEFAULT_TIMEOUT):
    """Run one openFDA field search.  Only single-field queries are reliable:
    mixing fields in one expression returns 500/404 on this endpoint."""
    payload = _get_json(
        OPENFDA_LABEL_URL, {"search": query, "limit": limit}, timeout=timeout
    )
    if not isinstance(payload, dict):
        return []
    return payload.get("results") or []


def _openfda_queries(term: str, include_substance: bool = True) -> list[str]:
    """The field queries to try for one search term.

    Brand and generic names match case-insensitively, but substance names are
    stored upper case ("ACETAMINOPHEN", not "paracetamol"), so that field is
    queried separately.  Multi-field expressions return 404/500 on this
    endpoint, hence one field per request.  The substance field is only asked
    for once the cheaper brand/generic fields came back empty.
    """
    text = str(term or "").strip()
    if not text:
        return []
    queries = [
        f'openfda.brand_name:"{text}"',
        f'openfda.generic_name:"{text}"',
    ]
    if include_substance:
        queries.append(f'openfda.substance_name:"{text.upper()}"')
    return queries




def _openfda_search_terms(name: str) -> list[str]:
    """The terms actually sent to openFDA, most useful first.

    A typed name rarely appears verbatim in one field ("Dolo 650",
    "Amoxicillin clavulanate"), and every extra guess costs another round trip.
    So the most identifying single word is sent, and the rest of the name is
    enforced locally by the relevance score.
    """
    terms: list[str] = []
    tokens = relevance_tokens(name)
    if tokens:
        terms.append(tokens[0])
    for term in search_terms(name):
        cleaned = normalize_query(term)
        if cleaned and cleaned.lower() not in {t.lower() for t in terms}:
            terms.append(cleaned)
    return terms



def _record_names(record: dict[str, Any]) -> dict[str, str]:
    meta = record.get("openfda") or {}
    return {
        "brand": _first(meta.get("brand_name")) or _first(record.get("brand_name")),
        "generic": _first(meta.get("generic_name")) or _first(record.get("generic_name")),
        "substance": _first(meta.get("substance_name")),
    }


def _score_record(term: str, record: dict[str, Any]) -> float:
    names = _record_names(record)
    return max(
        name_score(term, names["brand"], _BRAND_WEIGHT),
        name_score(term, names["generic"], _GENERIC_WEIGHT),
        name_score(term, names["substance"], _SUBSTANCE_WEIGHT),
    )


def _parse_openfda(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("openfda") or {}
    names = _record_names(record)
    ingredient = _first(record.get("active_ingredient")) or names["substance"]
    return {
        "brand_names": _dedupe(
            [names["brand"], _first(record.get("brand_name"))]
        ),
        "generic_name": title_case(names["generic"]),
        "substance": title_case(names["substance"]),
        "manufacturer": _first(meta.get("manufacturer_name")),
        "form": title_case(_first(record.get("dosage_form")))
        or _first(record.get("product_type")),
        "route": _first(meta.get("route")).title(),
        # Every label section arrives as a list of paragraphs; only the first
        # one is shown, condensed.
        "purpose": condense(_first(record.get("purpose")), 400),
        "uses": condense(_first(record.get("indications_and_usage")), 700),
        "warnings": condense(_first(record.get("warnings")), 700),
        "side_effects": condense(_first(record.get("adverse_reactions")), 620),
        "interactions": condense(_first(record.get("drug_interactions")), 560),
        "precautions": condense(
            _first(record.get("contraindications"))
            or _first(record.get("precautions"))
            or _first(record.get("boxed_warning")),
            560,
        ),
        "active_ingredient": condense(ingredient, 220),
    }


def _openfda_wave(
    term: str, timeout: float, include_substance: bool = True
) -> list[dict[str, Any]]:
    queries = _openfda_queries(term, include_substance)
    payloads = _gather(
        [
            lambda q=query: _openfda_search(q, OPENFDA_LIMIT, timeout)
            for query in queries
        ],
        deadline=timeout + 1.0,
    )
    records: list[dict[str, Any]] = []
    for payload in payloads:
        for record in payload or []:
            if isinstance(record, dict):
                records.append(record)
    return records


def lookup_openfda(name: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Fetch the official label for a brand or generic name.

    Candidates are always scored against the *whole* typed name, so searching
    the first word of "Amoxicillin clavulanate" still only accepts a product
    that is really about amoxicillin AND clavulanate.  The best-scoring label
    wins, not simply the first one that is close enough.
    """
    for term in _openfda_search_terms(name):
        # Brand and generic names come first: they cover almost every search,
        # and skipping the substance query when they already matched keeps a
        # successful lookup to a single round of requests.
        for include_substance in (False, True):
            best_record = None
            best_score = 0.0
            for record in _openfda_wave(term, timeout, include_substance):
                score = _score_record(name, record)
                if score > best_score:
                    best_score = score
                    best_record = record
            if best_record is not None and best_score >= _OPENFDA_MIN_SCORE:
                parsed = _parse_openfda(best_record)
                parsed["_matched_name"] = _best_matched_name(name, best_record)
                return parsed
    return {}


def _best_matched_name(name: str, record: dict[str, Any]) -> str:
    """The name inside the label that best explains the user's query."""
    names = _record_names(record)
    best_name = ""
    best = 0.0
    for field, weight in (
        ("brand", _BRAND_WEIGHT),
        ("generic", _GENERIC_WEIGHT),
        ("substance", _SUBSTANCE_WEIGHT),
    ):
        score = name_score(name, names[field], weight)
        if score > best:
            best = score
            best_name = names[field]
    return best_name



# ============================================================
# Source 2 — RxNorm (NIH)
# ============================================================

def _rxnorm_candidates(term: str, timeout: float) -> list[dict[str, str]]:
    payload = _get_json(
        f"{RXNORM_URL}/approximateTerm.json",
        {"term": term, "maxEntries": 8, "option": 1},
        timeout=timeout,
    )
    group = (payload or {}).get("approximateGroup") or {}
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in group.get("candidate") or []:
        rxcui = entry.get("rxcui")
        entry_name = entry.get("name")
        if not rxcui or not entry_name or str(rxcui) in seen:
            continue
        seen.add(str(rxcui))
        candidates.append({"rxcui": str(rxcui), "name": str(entry_name)})
    return candidates


def _rxnorm_properties(rxcui: str, timeout: float) -> dict[str, str]:
    payload = _get_json(f"{RXNORM_URL}/rxcui/{rxcui}/properties.json", timeout=timeout)
    props = (payload or {}).get("properties") or {}
    return {
        "name": _first(props.get("name")),
        "synonym": _first(props.get("synonym")),
        "tty": _first(props.get("tty")),
    }


def _rxnorm_related(rxcui: str, timeout: float) -> list[dict[str, str]]:
    # RxNorm wants a literal "+" between term types; requests would escape it,
    # so the query string is built by hand.
    url = (
        f"{RXNORM_URL}/rxcui/{rxcui}/related.json?tty="
        + "+".join(_RXNORM_RELATED_TTY)
    )
    payload = _get_json(url, timeout=timeout)
    groups = ((payload or {}).get("relatedGroup") or {}).get("conceptGroup") or []
    details: list[dict[str, str]] = []
    for group in groups:
        tty = str(group.get("tty") or "")
        for entry in group.get("conceptProperties") or []:
            if entry.get("name"):
                details.append(
                    {
                        "name": str(entry["name"]).title(),
                        "type": _RXNORM_TTY_LABELS.get(tty, tty or "Name"),
                    }
                )
    return details


def lookup_rxnorm(name: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Normalise the name to an ingredient and list other known names."""
    for term in search_terms(name):
        for candidate in _rxnorm_candidates(term, timeout):
            if not is_relevant(term, candidate["name"]):
                continue
            props = _rxnorm_properties(candidate["rxcui"], timeout)
            details = _rxnorm_related(candidate["rxcui"], timeout)
            names = _dedupe(
                [props.get("name"), props.get("synonym"), candidate["name"]]
                + [detail["name"] for detail in details]
            )
            if not any(is_relevant(term, value) for value in names):
                continue
            return {
                "standard_name": props.get("name") or candidate["name"],
                "term_type": _RXNORM_TTY_LABELS.get(
                    props.get("tty", ""), props.get("tty", "")
                ),
                "related_names": _dedupe(
                    detail["name"] for detail in details
                )[:8],
                "related_detail": details[:8],
            }
    return {}


# ============================================================
# Source 3 — Wikipedia
# ============================================================

_WIKI_LANGUAGE_ORDER = {
    "te": ("te", "en"),
    "hi": ("hi", "en"),
    "en": ("en",),
}


def _looks_like_medicine(summary: dict[str, Any]) -> bool:
    """Reject articles about a place or a person that share the drug's name."""
    haystack = f"{summary.get('title', '')} {summary.get('extract', '')}".lower()
    return any(word in haystack for word in _MEDICINE_CONTEXT_WORDS)


def _wiki_summary(language: str, title: str, timeout: float) -> dict[str, Any] | None:
    payload = _get_json(
        f"https://{language}.wikipedia.org/api/rest_v1/page/summary/"
        + quote(title.replace(" ", "_")),
        timeout=timeout,
    )
    if not isinstance(payload, dict) or payload.get("type") == "disambiguation":
        return None
    extract = str(payload.get("extract") or "").strip()
    if not extract:
        return None
    content_urls = payload.get("content_urls") or {}
    return {
        "title": str(payload.get("title") or title),
        "extract": extract,
        "url": str(
            (content_urls.get("desktop") or {}).get("page")
            or f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
        ),
        "image": str((payload.get("thumbnail") or {}).get("source") or ""),
        "language": language,
    }


def _wiki_search(term: str, timeout: float) -> str:
    payload = _get_json(
        WIKIPEDIA_API_URL,
        {
            "action": "query",
            "list": "search",
            "srsearch": f"{term} medicine",
            "format": "json",
            "srlimit": 1,
        },
        timeout=timeout,
    )
    hits = ((payload or {}).get("query") or {}).get("search") or []
    return str(hits[0].get("title") or "") if hits else ""


def _wiki_for_terms(terms: list[str], language: str, timeout: float) -> dict[str, Any]:
    order = _WIKI_LANGUAGE_ORDER.get(language, ("en",))
    for term in terms:
        if not term:
            continue
        direct = _gather(
            [
                lambda lang=wiki_language: _wiki_summary(lang, term, timeout)
                for wiki_language in order
            ],
            deadline=timeout,
        )
        for summary in direct:
            if (
                summary
                and is_relevant(term, summary["title"], summary["extract"])
                and _looks_like_medicine(summary)
            ):
                return summary
        # Brand names rarely have their own article, so fall back to a keyword
        # search and only accept a hit that is really about this drug.
        title = _wiki_search(term, timeout)
        if not title:
            continue
        found = _gather(
            [
                lambda lang=wiki_language: _wiki_summary(lang, title, timeout)
                for wiki_language in order
            ],
            deadline=timeout,
        )
        for summary in found:
            if (
                summary
                and is_relevant(term, summary["title"], summary["extract"])
                and _looks_like_medicine(summary)
            ):
                return summary
    return {}


def _wiki_terms(name: str, extra_terms: list[str] | None = None) -> list[str]:
    """Order Wikipedia lookups from most to least likely to be an article.

    A brand name ("Dolo 650") usually has no article while the generic name the
    drug databases resolved it to does ("Acetaminophen"), so the resolved names
    come first.  Terms that share a first word are collapsed, because the
    second one is very unlikely to find a different article.
    """
    ordered: list[str] = []
    seen_first_words: set[str] = set()
    for term in list(extra_terms or []) + search_terms(name):
        cleaned = normalize_query(term)
        if not cleaned:
            continue
        first = relevance_tokens(cleaned)
        key = first[0] if first else cleaned.lower()
        if key in seen_first_words:
            continue
        seen_first_words.add(key)
        ordered.append(cleaned)
    return ordered


def lookup_wikipedia(
    name: str,
    language: str = "en",
    timeout: float = DEFAULT_TIMEOUT,
    extra_terms: list[str] | None = None,
) -> dict[str, Any]:
    """Plain-language summary for a medicine, preferring the user's language."""
    return _wiki_for_terms(_wiki_terms(name, extra_terms), language, timeout)



# ============================================================
# Public API
# ============================================================

def web_search_links(
    name: str, wiki_url: str = "", language: str = "en"
) -> list[dict[str, str]]:
    """Build the 'look it up anywhere' links that make this a web search."""
    query = quote(str(name or "").strip())
    google_lang = language if language in ("te", "hi") else "en"
    links = []
    if wiki_url:
        links.append(
            {
                "label": "📖 Wikipedia",
                "hint": "Full background article",
                "url": wiki_url,
            }
        )
    links.extend(
        [
            {
                "label": "🔍 Google",
                "hint": "Search the web for this medicine",
                "url": f"https://www.google.com/search?q={query}+medicine&hl={google_lang}",
            },
            {
                "label": "🖼️ Google Images",
                "hint": "Photos of the strip, box, and packaging",
                "url": f"https://www.google.com/search?tbm=isch&q={query}",
            },
            {
                "label": "💊 Price & brands (1mg)",
                "hint": "Indian prices, brands, and substitutes",
                "url": f"https://1mg.com/drugs/search?search={query}",
            },
            {
                "label": "🛒 PharmEasy",
                "hint": "Pharmacy availability and price",
                "url": f"https://www.pharmeasy.in/search?q={query}",
            },
            {
                "label": "🧾 MedPlus",
                "hint": "Nearby pharmacy stock",
                "url": f"https://www.medplus.com/search?q={query}",
            },
        ]
    )
    return links


def _merge(
    label: dict[str, Any],
    rxnorm: dict[str, Any],
    wiki: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    brand_names = _dedupe(
        list(label.get("brand_names") or []) + [label.get("generic_name") or ""]
    )
    display_name = (
        label.get("_matched_name")
        or wiki.get("title")
        or rxnorm.get("standard_name")
        or str(name)
    )
    sources = [
        source
        for source, present in (
            ("openFDA", bool(label)),
            ("RxNorm", bool(rxnorm)),
            ("Wikipedia", bool(wiki)),
        )
        if present
    ]
    # A fuzzy hit is worth showing, but the user should know the database may
    # have matched a different product (e.g. "Pan 40" -> "Pan-Zyme-S").
    confidence = "none"
    if sources:
        cleaned_query = normalize_query(name).lower()
        cleaned_match = normalize_query(display_name).lower()
        confidence = "high" if cleaned_match and cleaned_match == cleaned_query else "partial"
    return {
        "query": str(name).strip(),
        "display_name": display_name or str(name).strip(),
        "matched": bool(sources),
        "confidence": confidence,
        "sources": sources,
        "brand_names": brand_names,
        "generic_name": label.get("generic_name", ""),
        "substance": label.get("substance", ""),
        "standard_name": rxnorm.get("standard_name", ""),
        "term_type": rxnorm.get("term_type", ""),
        "related_names": rxnorm.get("related_names", []),
        "related_detail": rxnorm.get("related_detail", []),
        "active_ingredient": label.get("active_ingredient", ""),
        "manufacturer": label.get("manufacturer", ""),
        "form": label.get("form", ""),
        "route": label.get("route", ""),
        "purpose": label.get("purpose", ""),
        "uses": label.get("uses", ""),
        "warnings": label.get("warnings", ""),
        "side_effects": label.get("side_effects", ""),
        "interactions": label.get("interactions", ""),
        "precautions": label.get("precautions", ""),
        "matched_name": label.get("_matched_name", ""),
        "description": wiki.get("extract", ""),
        "wiki_title": wiki.get("title", ""),
        "wiki_url": wiki.get("url", ""),
        "image_url": wiki.get("image", ""),
    }


def _empty_result(name: str) -> dict[str, Any]:
    return _merge({}, {}, {}, name)


@lru_cache(maxsize=256)
def _lookup_cached(name: str, language: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()

    def remaining() -> float:
        return STAGE_DEADLINE - (time.monotonic() - started)

    pool = ThreadPoolExecutor(max_workers=3)
    try:
        # openFDA and Wikipedia are the two sources worth waiting for, so they
        # gate the answer.  RxNorm only adds alternative names and is often
        # slow, so it is collected opportunistically instead of blocking.
        label_future = pool.submit(_safe_call, lambda: lookup_openfda(name, timeout))
        wiki_future = pool.submit(
            _safe_call, lambda: lookup_wikipedia(name, language, timeout)
        )
        rxnorm_future = pool.submit(_safe_call, lambda: lookup_rxnorm(name, timeout))

        label = _await(label_future, remaining()) or {}
        wiki = _await(wiki_future, remaining()) or {}

        # A brand name usually has no article of its own, so if Wikipedia found
        # nothing, retry with the generic name the drug databases resolved it to
        # ("Dolo 650" -> "Acetaminophen").  This only runs when it can add
        # something, which keeps the common case down to one round of calls.
        if not wiki and label.get("generic_name"):
            wiki = (
                _await(
                    pool.submit(
                        _safe_call,
                        lambda: lookup_wikipedia(
                            name,
                            language,
                            timeout,
                            [label.get("generic_name", ""), label.get("substance", "")],
                        ),
                    ),
                    WIKI_RETRY_DEADLINE,
                )
                or {}
            )

        rxnorm = rxnorm_future.result() if rxnorm_future.done() else {}
        rxnorm = rxnorm or {}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    result = _merge(label, rxnorm, wiki, name)
    result["web_links"] = web_search_links(name, result["wiki_url"], language)
    return result




def search_medicine(
    name: str, language: str = "en", timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    """Look a medicine name up across every source and merge the results.

    Returns the same keys on success and on failure, so callers can render the
    result without handling exceptions.  ``matched`` is False when no source
    recognised the name.
    """
    clean_name = re.sub(r"\s+", " ", str(name or "")).strip()
    if len(clean_name) < 2:
        empty = _empty_result(clean_name)
        empty["web_links"] = web_search_links(clean_name, "", language)
        return empty

    try:
        return dict(_lookup_cached(clean_name, language, timeout))
    except Exception:
        empty = _empty_result(clean_name)
        empty["web_links"] = web_search_links(clean_name, "", language)
        return empty


def clear_cache() -> None:
    """Drop cached lookups (used by tests and manual refreshes)."""
    _lookup_cached.cache_clear()
