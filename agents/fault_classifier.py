"""fault_classifier.py — Maps free-text fault descriptions to fault modes.

Two-stage hybrid classification: exact keyword match first, then semantic
similarity against the fault taxonomy (local embeddings + FAISS). No LLM in the
classification decision; guess_unknown_fault() uses an LLM only to name a fault
already established as novel."""
import os
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()
from app_config import get_threshold

VALID_FAULT_MODES = [
    "outer_race_fault", "inner_race_fault", "lubrication_issue",
    "imbalance", "misalignment", "cage_fault", "unknown",
]

FAULT_DESCRIPTIONS = {
    "outer_race_fault": "Damage on the outer (stationary) ring of the bearing — often grinding, scraping, or rumbling that worsens under load.",
    "inner_race_fault": "Damage on the inner (rotating) ring — vibration that changes with load and speed.",
    "lubrication_issue": "Low, degraded, or contaminated oil/grease causing rising friction and temperature.",
    "imbalance": "Uneven mass in the rotating assembly causing steady vibration at running speed.",
    "misalignment": "Shaft or coupling not properly aligned, causing vibration and coupling wear.",
    "cage_fault": "Damage to the cage/retainer spacing the rolling elements — often an irregular, intermittent rattle.",
}

import json
import faiss
from rag.document_store import _embed

# Keyword/synonym map for fast, exact Stage-1 classification (data, not logic).
# "friction" deliberately excluded — too ambiguous, left to semantic → novel.
_FAULT_SYNONYMS = {
    "lubrication_issue": ["lubrication", "lube", "lubricant", "oil degradation",
                          "oil breakdown", "low oil", "grease", "greasing", "oil",
                          "dry running", "overdue lube", "insufficient lubrication"],
    "outer_race_fault":  ["outer race", "outer ring", "outer raceway", "bpfo",
                          "grinding", "scraping", "rumbling", "race damage"],
    "inner_race_fault":  ["inner race", "inner ring", "inner raceway", "bpfi"],
    "cage_fault":        ["cage", "retainer", "rattling", "clicking",
                          "intermittent rattle", "ftf"],
    "imbalance":         ["imbalance", "unbalance", "rotor imbalance", "1x",
                          "one-x", "material loss", "out of balance"],
    "misalignment":      ["misalignment", "misaligned", "alignment", "shaft offset",
                          "coupling wear", "coupling", "2x", "two-x", "axial vibration"],
}
_SEMANTIC_THRESHOLD = get_threshold("semantic_classifier_threshold", 0.45)

# Lazily-built in-memory index over the 6 fault definitions (built once).
_fault_index = None
_fault_modes_list = None

def _build_fault_index():
    """Build the in-memory embedding index of fault definitions once (lazy; no-op if already built)."""
    global _fault_index, _fault_modes_list
    if _fault_index is not None:
        return
    with open("data/fault_taxonomy.json", encoding="utf-8") as f:
        taxonomy = json.load(f)
    _fault_modes_list = [e.get("fault_mode") for e in taxonomy]
    texts = [
        f"{e.get('fault_mode','').replace('_',' ')} — affected component: "
        f"{e.get('affected_component','').replace('_',' ')} — typical causes: "
        f"{', '.join(e.get('typical_causes',[]) or [])}"
        for e in taxonomy
    ]
    vecs = _embed(texts)
    faiss.normalize_L2(vecs)
    idx = faiss.IndexFlatIP(vecs.shape[1])
    idx.add(vecs)
    _fault_index = idx

def _keyword_match(desc: str):
    """Return (mode, matched_phrase) if exactly one synonym set matches; None if zero or ambiguous."""
    d = desc.lower()
    hits = {}
    for mode, phrases in _FAULT_SYNONYMS.items():
        for p in sorted(phrases, key=len, reverse=True):
            if p in d:
                hits[mode] = p
                break
    if len(hits) == 1:
        m = next(iter(hits))
        return m, hits[m]
    return None  # 0 or 2+ matches → semantic

def _semantic_match(desc: str, k: int = 3):
    """Return the top-k (fault_mode, similarity) matches for the description, most similar first."""
    _build_fault_index()
    qv = _embed([desc]); faiss.normalize_L2(qv)
    s, i = _fault_index.search(qv, k)
    return [(_fault_modes_list[j], float(sc)) for sc, j in zip(s[0], i[0])]


def classify_fault(description: str) -> Dict:
    """
    Classify a free-text fault description into a fault mode using
    keyword (Stage 1) + semantic retrieval (Stage 2). No LLM.
    Returns {"fault_mode": str, "reason": str, "alternatives": list[str]}.
    Returns fault_mode="unknown" when nothing matches confidently
    (which routes to the novel path + guess_unknown_fault).
    """
    if not description or not description.strip():
        return {"fault_mode": "unknown", "reason": "No description provided.", "alternatives": []}
    try:
        # Stage 1: keyword / exact synonym match
        kw = _keyword_match(description)
        if kw:
            mode, phrase = kw
            return {"fault_mode": mode,
                    "reason": f"Matched on keyword '{phrase}'.",
                    "alternatives": []}
        # Stage 2: semantic match against the 6 fault definitions
        ranked = _semantic_match(description)
        top_mode, top_score = ranked[0]
        if top_score >= _SEMANTIC_THRESHOLD:
            alts = [m for m, sc in ranked[1:] if sc >= 0.30 and m in VALID_FAULT_MODES]
            return {"fault_mode": top_mode,
                    "reason": f"Closest documented fault by meaning (similarity {top_score:.2f}).",
                    "alternatives": alts}
        # Below threshold → unknown → novel path
        return {"fault_mode": "unknown",
                "reason": f"No confident match (closest {top_mode} at {top_score:.2f}); treating as novel.",
                "alternatives": []}
    except Exception as e:
        return {"fault_mode": "unknown", "reason": f"Classification error ({e}); treating as novel.", "alternatives": []}


def guess_unknown_fault(description: str) -> dict:
    """
    For a description that did NOT match any of the 6 standard modes,
    have the LLM name the most likely fault in free-form language.
    Returns {"likely_fault": str, "explanation": str}.
    Safe fallback on any error.
    """
    if not description or not description.strip():
        return {"likely_fault": "Unrecognized fault",
                "explanation": "No description was provided."}
    try:
        from langchain_openai import AzureChatOpenAI
        llm = AzureChatOpenAI(
            openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
            temperature=0.2,
            max_tokens=200,
        )
    except Exception as e:
        return {"likely_fault": "Unrecognized fault",
                "explanation": f"AI unavailable ({e}); manual diagnosis needed."}

    prompt = (
        "An industrial maintenance fault was described that does NOT match any of "
        "the system's six standard bearing/rotating-machinery fault modes "
        "(outer race, inner race, lubrication, imbalance, misalignment, cage). "
        "In plain language, name the SINGLE most likely fault this description points to, "
        "even if it is outside the standard set (e.g. a gear-mesh/tooth-wear fault, "
        "seal failure, contamination, electrical, structural, etc.). "
        "Be specific but honest that this is an interpretation.\n\n"
        f"Description: \"{description}\"\n\n"
        "Respond in EXACTLY this format (no other text):\n"
        "LIKELY_FAULT: <short fault name, 2-6 words>\n"
        "EXPLANATION: <one sentence on why this fits>"
    )
    try:
        resp = llm.invoke(prompt)
        text = resp.content if hasattr(resp, "content") else str(resp)
        likely, expl = "Unrecognized fault", "See description."
        for line in text.splitlines():
            line = line.strip()
            if line.upper().startswith("LIKELY_FAULT:"):
                likely = line.split(":", 1)[1].strip()
            elif line.upper().startswith("EXPLANATION:"):
                expl = line.split(":", 1)[1].strip()
        return {"likely_fault": likely or "Unrecognized fault",
                "explanation": expl}
    except Exception as e:
        return {"likely_fault": "Unrecognized fault",
                "explanation": f"AI unavailable ({e}); manual diagnosis needed."}
