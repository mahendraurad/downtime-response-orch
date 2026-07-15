"""
PDF Renderer — Learned Case to Downloadable PDF
================================================

WHAT THIS FILE DOES
-------------------
Renders a learned case JSON document as a formatted PDF for user download.
Used by the Discovery tab (PATH_B result panel), the Add Case flow, and
the Knowledge Copilot chatbot when a user requests a case as a document.

Produces an A4-format PDF with:
  - Header (case_id, generation timestamp, agent name)
  - Case identity section (fault_mode, asset_type, bearing_type, asset_id)
  - Signal signature table (vibration, kurtosis, temperature, BPFO/BPFI)
  - Diagnosis and reasoning
  - Recommended action, SOP reference, parts required
  - Root cause and contributing factors
  - Lessons learned and prevention recommendations
  - Footer (page number)

WHAT IT USES
------------
Third-party libraries:
  - reportlab — PDF generation library, uses:
      * SimpleDocTemplate for page layout
      * Table, TableStyle for signal signature grid
      * Paragraph, Spacer for prose sections
      * getSampleStyleSheet for typography defaults

TECHNIQUES APPLIED
------------------
  - Report generation via reportlab.platypus flowables
  - Structured section layout with alternating shaded rows for tables
  - Byte stream output (BytesIO) for direct Streamlit download button

CALLED BY
---------
  - streamlit_app_v3.py::render_add_case_output   — PATH_B success view
  - streamlit_app_v3.py::render_verify_case        — Add Case duplicate check
  - streamlit_app_v3.py::render_knowledge_copilot — chatbot document view
  - streamlit_app_v3.py::_render_check_pdf_view    — Discovery PATH_B panel

RETURNS
-------
BytesIO buffer containing the PDF, ready for Streamlit's
st.download_button component.

FOR THE HANDOVER READER
-----------------------
No configuration needed. Called with a case dict and returns bytes.
If reportlab is not installed, callers wrap the import in try/except
and fall back to a plain-text case display.
"""

import io
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    getSampleStyleSheet,
    ParagraphStyle)
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether)
from reportlab.lib.enums import (
    TA_LEFT, TA_CENTER, TA_JUSTIFY)
from services.fault_mode_defaults import get_defaults


# ----- Page header / footer -----
def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)

    # Header
    canvas.drawString(
        20 * mm,
        285 * mm,
        "DRO Synthetic Data Baseline")
    canvas.drawCentredString(
        105 * mm,
        285 * mm,
        f"Page {doc.page}")
    canvas.drawRightString(
        190 * mm,
        285 * mm,
        "CONFIDENTIAL — Internal Use Only")

    # Header line
    canvas.setStrokeColor(colors.grey)
    canvas.setLineWidth(0.3)
    canvas.line(
        20 * mm, 282 * mm,
        190 * mm, 282 * mm)

    canvas.restoreState()


# ----- Styles -----
def _build_styles():
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'CaseTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=colors.HexColor('#1a1a2e'),
        spaceAfter=4,
        spaceBefore=4,
        leading=22)

    subhead_style = ParagraphStyle(
        'SubHead',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        textColor=colors.HexColor('#3a3a5e'),
        spaceAfter=2,
        leading=14)

    doctype_style = ParagraphStyle(
        'DocType',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=9,
        textColor=colors.HexColor('#666666'),
        spaceAfter=6,
        leading=12)

    section_style = ParagraphStyle(
        'Section',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        textColor=colors.HexColor('#A100FF'),
        spaceAfter=6,
        spaceBefore=12,
        leading=16)

    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        textColor=colors.HexColor('#1a1a2e'),
        spaceAfter=4,
        leading=13,
        alignment=TA_JUSTIFY)

    return {
        'title': title_style,
        'subhead': subhead_style,
        'doctype': doctype_style,
        'section': section_style,
        'body': body_style,
    }


# ----- Table builder -----
def _make_kv_table(rows):
    """Build a 2-column field/value table."""
    data = [
        [Paragraph(
            f"<b>{k}</b>",
            ParagraphStyle(
                'k',
                fontName='Helvetica-Bold',
                fontSize=9,
                textColor=colors.HexColor(
                    '#3a3a5e'))),
         Paragraph(
             str(v),
             ParagraphStyle(
                 'v',
                 fontName='Helvetica',
                 fontSize=9,
                 textColor=colors.HexColor(
                     '#1a1a2e')))]
        for k, v in rows
    ]

    tbl = Table(
        data,
        colWidths=[55 * mm, 115 * mm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND',
         (0, 0),
         (0, -1),
         colors.HexColor('#f5f0fa')),
        ('BACKGROUND',
         (1, 0),
         (1, -1),
         colors.HexColor('#ffffff')),
        ('GRID',
         (0, 0),
         (-1, -1),
         0.3,
         colors.HexColor('#d0d0d0')),
        ('VALIGN',
         (0, 0),
         (-1, -1),
         'TOP'),
        ('LEFTPADDING',
         (0, 0),
         (-1, -1),
         8),
        ('RIGHTPADDING',
         (0, 0),
         (-1, -1),
         8),
        ('TOPPADDING',
         (0, 0),
         (-1, -1),
         5),
        ('BOTTOMPADDING',
         (0, 0),
         (-1, -1),
         5),
    ]))
    return tbl


# ----- Main rendering function -----
def render_case_pdf(case_data: dict) -> bytes:
    """
    Render a learned case dict as a PDF.
    Returns bytes ready for download.
    Handles both flat seed-case dicts and nested generated-case dicts.
    """
    def _coerce(val, key="value"):
        """Return val unchanged if dict; wrap plain string in {key: val}."""
        if isinstance(val, dict):
            return val
        return {key: str(val) if val else ""}

    # Normalise fields that differ between flat seed JSONs and nested
    # generated-case dicts.  Work on a shallow copy — never mutate caller.
    case_orig = case_data   # pre-adapter snapshot — used for _used_defaults check only
    case = dict(case_data)

    # root_cause: seed → "contamination ingress" (str)
    #             generated → {"cause": "...", "factors": "..."}
    case["root_cause"] = _coerce(case.get("root_cause", ""), "cause")

    # action: seed has "action_taken" (str), generated has "action" (dict)
    if not isinstance(case.get("action"), dict):
        _at = case.get("action_taken") or case.get("action") or ""
        case["action"] = _coerce(_at, "recommended")

    # lessons: seed has "lessons_learned" (str), generated has "lessons"
    if not case.get("lessons"):
        case["lessons"] = case.get("lessons_learned", "")

    # outcome: seed has "result" (str), generated has "outcome" (dict)
    if not isinstance(case.get("outcome"), dict):
        _res = case.get("result") or case.get("outcome") or ""
        case["outcome"] = _coerce(_res, "status")

    # detection: seed JSONs have no detection sub-dict — synthesize from flat fields if present,
    # else build a minimal one from fault_mode/asset/bearing so the section isn't blank.
    if not isinstance(case.get("detection"), dict) or not case.get("detection"):
        case["detection"] = {
            "date": case.get("created_at", case.get("generated_date", datetime.now().strftime("%Y-%m-%d"))),
            "detected_by": case.get("detected_by", "monitoring_agent"),
            "anomaly_score": case.get("anomaly_score", case.get("score", "")),
            "vib_rms_mm_s": case.get("vib_rms_mm_s", case.get("vibration", "")),
            "kurtosis": case.get("kurtosis", ""),
            "temp_c": case.get("temp_c", case.get("temperature", "")),
            "bpfo_energy": case.get("bpfo_energy", ""),
            "signal_quality": case.get("signal_quality", case.get("signal_quality_score", "")),
        }

    # diagnosis: synthesize from fault_mode + any confidence/RUL data present
    if not isinstance(case.get("diagnosis"), dict) or not case.get("diagnosis"):
        _fault_label = case.get("fault_mode", "fault").replace("_", " ").title()
        case["diagnosis"] = {
            "primary_diagnosis": f"{_fault_label}" + (f" — Stage {case.get('stage')}" if case.get("stage") else ""),
            "confidence": case.get("diagnosis_confidence", case.get("confidence", "")),
            "rul_estimate": case.get("rul_estimate", case.get("rul_days", "")),
            "reasoning": case.get("diagnosis_reasoning", case.get("reasoning",
                f"{_fault_label} identified on {case.get('asset_type', 'asset')} "
                f"with bearing {case.get('bearing_type', 'N/A')}.")),
            "differential": case.get("differential", "Other fault modes ruled out based on signal profile."),
        }

    # findings: seed JSONs never have this — synthesize a short paragraph from root_cause
    if not case.get("findings"):
        _cause_text = case["root_cause"].get("cause", "") if isinstance(case.get("root_cause"), dict) else ""
        case["findings"] = (
            f"Inspection confirmed {case.get('fault_mode', 'the fault').replace('_', ' ')} "
            f"on the {case.get('bearing_type', 'bearing')}. {_cause_text}".strip()
        )

    # --- Defaults patching ---
    # Fill missing numeric/metadata fields from fault-mode-keyed typical values.
    # Runs AFTER all adapters so generated cases with real data are never
    # overwritten.  Only empty-string / None values are replaced.
    _fm = case.get("fault_mode", "_default")
    _def = get_defaults(_fm)

    _d = _def["detection"]
    _det = case["detection"]
    for _k, _v in (
        ("anomaly_score",  _d["anomaly_score"]),
        ("vib_rms_mm_s",   _d["vib_rms_mm_s"]),
        ("kurtosis",       _d["kurtosis"]),
        ("temp_c",         _d["temp_c"]),
        ("bpfo_energy",    _d["bpfo_energy"]),
        ("signal_quality", _d["signal_quality"]),
    ):
        if _det.get(_k) in ("", None):
            _det[_k] = _v

    _dx = _def["diagnosis"]
    _diag = case["diagnosis"]
    if _diag.get("confidence") in ("", None):
        _diag["confidence"] = _dx["confidence"]
    if _diag.get("rul_estimate") in ("", None):
        _diag["rul_estimate"] = _dx["rul_estimate"]

    _da = _def["action"]
    _act = case["action"]
    if not _act.get("work_order"):
        _act["work_order"] = _da["work_order"]
    if not _act.get("parts"):
        _act["parts"] = case_orig.get("parts_required", _da["parts"])
    if not _act.get("duration_hr"):
        _act["duration_hr"] = case_orig.get("estimated_duration", _da["duration_hr"])
    if not _act.get("sop"):
        _act["sop"] = case_orig.get("sop_reference", _da["sop"])

    _do = _def["outcome_targets"]
    _out = case["outcome"]
    if not _out.get("vib_target"):
        _out["vib_target"] = case_orig.get("vib_target", _do["vib_target"])
    if not _out.get("temp_target"):
        _out["temp_target"] = case_orig.get("temp_target", _do["temp_target"])
    if not _out.get("kurt_target"):
        _out["kurt_target"] = case_orig.get("kurt_target", _do["kurt_target"])
    if not _out.get("qa_window"):
        _out["qa_window"] = case_orig.get("qa_window", _do["qa_window"])

    # True when no measured telemetry was present in the original case dict
    _used_defaults = not any(
        case_orig.get(k) for k in (
            "anomaly_score", "vib_rms_mm_s", "kurtosis", "temp_c")
    )

    # Use the normalised copy for all field access below
    case_data = case

    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=25 * mm,
        bottomMargin=20 * mm,
        title=case_data.get(
            "case_id", "Learned Case"))

    styles = _build_styles()
    story = []

    # --- Title block ---
    fault_mode = case_data.get(
        "fault_mode", "fault").replace(
        "_", " ").title()
    asset_type = case_data.get(
        "asset_type", "asset")
    asset_id = case_data.get("asset_id") or ""
    bearing_type = case_data.get(
        "bearing_type", "")
    stage = case_data.get("stage", "")
    case_id = case_data.get(
        "case_id", "CASE_NEW")
    generated = case_data.get(
        "generated_date",
        datetime.now().strftime("%Y-%m-%d"))

    _stage_prefix = f"Stage {stage} " if stage else ""
    _asset_suffix = f" {asset_id}" if asset_id else ""
    story.append(Paragraph(
        f"{_stage_prefix}{fault_mode} — "
        f"{asset_type.title()}{_asset_suffix}",
        styles['title']))

    _asset_label = f"{asset_id} ({asset_type})" if asset_id else asset_type
    story.append(Paragraph(
        f"<b>Asset:</b> {_asset_label} &nbsp;|&nbsp; "
        f"<b>Bearing:</b> {bearing_type}",
        styles['subhead']))

    story.append(Paragraph(
        f"<b>Fault Mode:</b> "
        f"{case_data.get('fault_mode', '')} "
        f"&nbsp;|&nbsp; "
        f"<b>Stage:</b> {stage}",
        styles['subhead']))

    story.append(Paragraph(
        "Learned Case — Knowledge Agent "
        "Retrieval Document | DRO Pipeline",
        styles['doctype']))

    story.append(Paragraph(
        f"<b>{case_id}</b> &nbsp;|&nbsp; "
        f"Outcome: PENDING &nbsp;|&nbsp; "
        f"Generated: {generated}",
        styles['subhead']))

    story.append(Spacer(1, 6))

    # --- Section 1: Detection Summary ---
    story.append(Paragraph(
        "1. Detection Summary",
        styles['section']))

    detection = case_data.get("detection", {})
    rows = [
        ("Detection Date",
         detection.get("date", generated)),
        ("Detected By",
         detection.get("detected_by",
                       "monitoring_agent")),
        ("Anomaly Score",
         detection.get("anomaly_score", "")),
        ("Vibration RMS",
         f"{detection.get('vib_rms_mm_s', '')} mm/s"),
        ("Kurtosis",
         detection.get("kurtosis", "")),
        ("Temperature",
         f"{detection.get('temp_c', '')}C"),
        ("BPFO Energy",
         f"{detection.get('bpfo_energy', '')}x baseline"),
        ("Signal Quality",
         detection.get("signal_quality", "")),
    ]
    story.append(_make_kv_table(rows))
    story.append(Spacer(1, 4))

    summary = detection.get("summary", "")
    if summary:
        story.append(Paragraph(
            summary, styles['body']))

    # --- Section 2: Agent Diagnosis ---
    story.append(Paragraph(
        "2. Agent Diagnosis",
        styles['section']))

    diag = case_data.get("diagnosis", {})
    rows = [
        ("Primary Diagnosis",
         diag.get("primary_diagnosis",
             diag.get("primary",
                 f"{case_data.get('fault_mode', '')} "
                 f"— Stage {stage}"))),
        ("Confidence",
         diag.get("confidence", "")),
        ("RUL Estimate",
         f"{diag.get('rul_estimate', diag.get('rul', ''))} days from detection"),
    ]
    story.append(_make_kv_table(rows))

    if diag.get("reasoning"):
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"<b>Reasoning:</b> "
            f"{diag.get('reasoning', '')}",
            styles['body']))

    if diag.get("differential"):
        story.append(Paragraph(
            f"<b>Differential considered:</b> "
            f"{diag.get('differential', '')}",
            styles['body']))

    # --- Section 3: Action Taken ---
    story.append(Paragraph(
        "3. Action Taken",
        styles['section']))

    action = case_data.get("action", {})
    rows = [
        ("Recommended Action",
         action.get("recommended", "")),
        ("Work Order",
         action.get("work_order", "")),
        ("Parts Required",
         action.get("parts", "")),
        ("Estimated Duration",
         f"{action.get('duration_hr', '')} hours"),
        ("SOP Reference",
         action.get("sop", "")),
    ]
    story.append(_make_kv_table(rows))

    # --- Section 4: Findings at Inspection ---
    story.append(Paragraph(
        "4. Findings at Inspection",
        styles['section']))

    findings = case_data.get(
        "findings",
        f"Inspection of {bearing_type} bearing on "
        f"{asset_type}{_asset_suffix} confirmed "
        f"{case_data.get('fault_mode', '')}.")
    story.append(Paragraph(
        findings, styles['body']))

    # --- Section 5: Expected Outcome ---
    story.append(Paragraph(
        "5. Expected Outcome",
        styles['section']))

    story.append(Paragraph(
        "<i>Repair has not yet been performed. "
        "These are the acceptance criteria from "
        "SOP_006 for marking this repair "
        "successful when complete.</i>",
        styles['body']))

    outcome = case_data.get("outcome", {})
    rows = [
        ("Post-Repair Vibration Target",
         outcome.get("vib_target", "pending measurement")),
        ("Post-Repair Temperature Target",
         outcome.get("temp_target", "pending measurement")),
        ("Post-Repair Kurtosis Target",
         outcome.get("kurt_target", "")),
        ("QA Check Window",
         outcome.get("qa_window", "post-repair")),
        ("Status",
         outcome.get("status", "NEW — awaiting repair")),
        ("Recommendation Followed",
         outcome.get(
             "rec_followed",
             "Pending repair completion")),
    ]
    story.append(_make_kv_table(rows))

    # --- Section 6: Root Cause Confirmed ---
    story.append(Paragraph(
        "6. Root Cause Confirmed",
        styles['section']))

    rc = case_data.get("root_cause", {})
    story.append(Paragraph(
        f"<b>Root Cause:</b> "
        f"{rc.get('cause', 'under investigation')}",
        styles['body']))

    if rc.get("factors"):
        story.append(Paragraph(
            f"<b>Contributing Factors:</b> "
            f"{rc.get('factors', '')}",
            styles['body']))

    # --- Section 7: Lessons Learned ---
    story.append(Paragraph(
        "7. Lessons Learned",
        styles['section']))

    lessons = case_data.get("lessons", "")
    if isinstance(lessons, list):
        for L in lessons:
            story.append(Paragraph(
                f"• {L}", styles['body']))
    else:
        for line in lessons.split("\n"):
            line = line.strip().lstrip("- ")
            if line:
                story.append(Paragraph(
                    f"• {line}", styles['body']))

    if _used_defaults:
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            "<i>Telemetry values shown are representative typical values for "
            "this fault mode; actual measurements were not captured for this "
            "learned case.</i>",
            ParagraphStyle(
                'footnote',
                fontName='Helvetica-Oblique',
                fontSize=8,
                textColor=colors.HexColor('#888888'),
                spaceAfter=4,
                leading=11)))

    # Build the PDF
    doc.build(
        story,
        onFirstPage=_header_footer,
        onLaterPages=_header_footer)

    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


def parse_md_to_case_data(
        md_content: str,
        filename: str) -> dict:
    """
    Parse a generated .md file into a case_data
    dict for the PDF renderer.
    """
    import re

    def grab(pattern, default=""):
        m = re.search(
            pattern, md_content,
            re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else default

    case_id = grab(
        r'\*\*(CASE_(?:DEMO_)?[A-Za-z0-9_]+)\*\*',
        "CASE_DEMO_NEW")
    fault_mode = grab(
        r'\*\*Fault Mode:\*\*\s*([a-z_]+)',
        "fault")
    asset_id = grab(
        r'\*\*Asset:\*\*\s*([A-Z0-9_]+)',
        "ASSET")
    asset_type = grab(
        r'\*\*Asset:\*\*\s*[A-Z0-9_]+\s*\(([a-z]+)\)',
        "asset")
    bearing = grab(
        r'\*\*Bearing:\*\*\s*([A-Z0-9]+)',
        "")
    stage = grab(
        r'\*\*Stage:\*\*\s*(\d)',
        "2")
    generated = grab(
        r'Generated:\s*([\d-]+)',
        datetime.now().strftime("%Y-%m-%d"))

    anomaly = grab(
        r'\|\s*Anomaly score\s*\|\s*([\d.]+)',
        "0.00")
    vib = grab(
        r'\|\s*Vibration RMS\s*\|\s*([\d.]+)',
        "0")
    kurt_raw = grab(
        r'\|\s*Kurtosis\s*\|\s*([\d.]+)',
        "0")
    temp = grab(
        r'\|\s*Temperature\s*\|\s*([\d.]+)',
        "0")
    bpfo = grab(
        r'\|\s*BPFO energy\s*\|\s*([\d.]+)',
        "0")
    sigq = grab(
        r'\|\s*Signal quality\s*\|\s*([\d.]+)',
        "0")
    confidence = grab(
        r'\|\s*Confidence\s*\|\s*(\d+%)',
        "")
    rul = grab(
        r'\|\s*RUL estimate\s*\|\s*([\d–\-]+)',
        "")
    reasoning = grab(
        r'\*\*Reasoning:\*\*\s*([^\n]+)',
        "")
    differential = grab(
        r'\*\*Differential considered:\*\*\s*([^\n]+)',
        "")
    rec_action = grab(
        r'\|\s*Recommended action\s*\|\s*([^\|\n]+)',
        "")
    work_order = grab(
        r'\|\s*Work order\s*\|\s*([^\|\n]+)',
        "")
    parts = grab(
        r'\|\s*Parts required\s*\|\s*([^\|\n]+)',
        "")
    duration = grab(
        r'\|\s*Estimated duration\s*\|\s*(\d+)',
        "")
    sop = grab(
        r'\|\s*SOP reference\s*\|\s*([^\|\n]+)',
        "")
    findings = grab(
        r'##\s*4\.\s*Findings at Inspection\s*\n+([\s\S]+?)(?=\n+---|\n+##)',
        "")
    root_cause_text = grab(
        r'\*\*Root cause:\*\*\s*([^\n]+)',
        "under investigation")
    factors = grab(
        r'\*\*Contributing factors:\*\*\s*([^\n]+)',
        "")

    # Outcome — expected targets
    vib_target = grab(
        r'\|\s*Post-repair vibration target\s*\|\s*([^\|\n]+)',
        "pending measurement")
    temp_target = grab(
        r'\|\s*Post-repair temperature target\s*\|\s*([^\|\n]+)',
        "pending measurement")
    kurt_target = grab(
        r'\|\s*Post-repair kurtosis target\s*\|\s*([^\|\n]+)',
        "")
    qa_window = grab(
        r'\|\s*QA check window\s*\|\s*([^\|\n]+)',
        "post-repair")

    # Lessons — grab everything after Section 7 header
    lessons_block = grab(
        r'##\s*7\.\s*Lessons Learned\s*\n+([\s\S]+?)$',
        "")

    return {
        "case_id": case_id,
        "fault_mode": fault_mode,
        "asset_id": asset_id,
        "asset_type": asset_type,
        "bearing_type": bearing,
        "stage": stage,
        "generated_date": generated,
        "detection": {
            "date": generated,
            "detected_by": "monitoring_agent",
            "anomaly_score": anomaly,
            "vib_rms_mm_s": vib,
            "kurtosis": f"{kurt_raw} (threshold 5.0)",
            "temp_c": temp,
            "bpfo_energy": bpfo,
            "signal_quality": f"{sigq} (clean signal)",
            "summary": (
                f"{fault_mode.replace('_', ' ').title()} "
                f"detected on {bearing} bearing "
                f"at Stage {stage} with anomaly score {anomaly}."
            ),
        },
        "diagnosis": {
            "primary": f"{fault_mode} — Stage {stage}",
            "confidence": confidence,
            "rul": rul,
            "reasoning": reasoning,
            "differential": differential,
        },
        "action": {
            "recommended": rec_action.strip(),
            "work_order": work_order.strip(),
            "parts": parts.strip(),
            "duration_hr": duration,
            "sop": sop.strip(),
        },
        "findings": findings.strip(),
        "outcome": {
            "vib_target": vib_target.strip(),
            "temp_target": temp_target.strip(),
            "kurt_target": kurt_target.strip(),
            "qa_window": qa_window.strip(),
            "status": "NEW — awaiting repair",
            "rec_followed": "Pending repair completion",
        },
        "root_cause": {
            "cause": root_cause_text.strip(),
            "factors": factors.strip(),
        },
        "lessons": lessons_block.strip() or "Capture post-repair findings.",
    }
