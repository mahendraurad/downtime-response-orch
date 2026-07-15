import os
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()

VALID_FAULT_MODES = [
    "outer_race_fault", "inner_race_fault", "lubrication_issue",
    "imbalance", "misalignment", "cage_fault", "unknown",
]


def classify_fault(description: str) -> Dict:
    """
    Classify a free-text fault description into a fault mode.
    Returns {"fault_mode": str, "reason": str, "alternatives": list[str]}.
    Falls back to fault_mode="imbalance" with a clear reason on any error.
    """
    if not description or not description.strip():
        return {"fault_mode": "imbalance", "reason": "No description provided.", "alternatives": []}

    try:
        from langchain_openai import AzureChatOpenAI
        llm = AzureChatOpenAI(
            openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
            temperature=0.0,
            max_tokens=150,
        )
    except Exception as e:
        return {"fault_mode": "imbalance", "reason": f"Classification unavailable ({e}); defaulted.", "alternatives": []}

    prompt = (
        f"You are a bearing fault classifier. Given a maintenance "
        f"fault description, classify it into EXACTLY ONE of these "
        f"fault modes:\n"
        f"{', '.join(VALID_FAULT_MODES)}\n\n"
        f"If the description does not reasonably describe ANY of the six modes above "
        f"(for example, a gearbox tooth fault, a contamination issue with no clear "
        f"lubrication link, a structural or electrical problem, or anything outside "
        f"rotating-bearing faults), classify it as: unknown\n\n"
        f"Key distinctions for commonly confused modes:\n"
        f"- outer_race_fault: damage or noise at the OUTER (stationary) ring or housing "
        f"side; grinding, scraping, or rumbling at the outer part of the bearing; "
        f"BPFO frequency signature. Grinding or scraping without mention of the cage "
        f"almost always points here.\n"
        f"- inner_race_fault: damage at the INNER (rotating) ring; vibration amplitude "
        f"varies with load/speed; BPFI frequency signature.\n"
        f"- cage_fault: the CAGE or RETAINER that spaces the rolling elements is damaged; "
        f"irregular, intermittent rattling or clicking rather than continuous grinding; "
        f"FTF (fundamental train frequency) signature. Only choose cage_fault if the "
        f"cage or retainer is specifically mentioned, or the symptom is distinctly "
        f"intermittent rattling — NOT metal-on-metal grinding (that is race damage).\n\n"
        f"Description: \"{description}\"\n\n"
        f"Respond in EXACTLY this format (no other text):\n"
        f"FAULT_MODE: <one of the modes, or 'unknown'>\n"
        f"REASON: <one sentence explaining why this mode fits best>\n"
        f"ALTERNATIVES: <comma-separated list of 1-2 other plausible modes, or NONE>\n\n"
        f"If the description is vague but still clearly points to one of the six "
        f"modes, use the closest match. But if it does not genuinely fit any of "
        f"the six, return 'unknown' rather than forcing a poor match."
    )
    try:
        resp = llm.invoke(prompt)
        text = resp.content if hasattr(resp, "content") else str(resp)
        fault_mode = "imbalance"
        reason = "Could not parse classification."
        alternatives: List[str] = []
        for line in text.splitlines():
            line = line.strip()
            if line.upper().startswith("FAULT_MODE:"):
                val = line.split(":", 1)[1].strip().lower()
                if val in VALID_FAULT_MODES:
                    fault_mode = val
            elif line.upper().startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()
            elif line.upper().startswith("ALTERNATIVES:"):
                raw = line.split(":", 1)[1].strip()
                if raw.upper() != "NONE":
                    alternatives = [
                        a.strip().lower() for a in raw.split(",")
                        if a.strip().lower() in VALID_FAULT_MODES
                    ]
        return {"fault_mode": fault_mode, "reason": reason, "alternatives": alternatives}
    except Exception as e:
        return {"fault_mode": "imbalance", "reason": f"Classification unavailable ({e}); defaulted.", "alternatives": []}


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
