"""
run_demo.py  —  Asset quality audit.
Each asset gets one score and one clear reason.

Usage:
    python run_demo.py            # all assets (output also saved to logs/)
    python run_demo.py --json     # machine-readable JSON (also saved to logs/)
    python run_demo.py --remediate # interactive: impute/drop FLAGGED records

Every run appends a timestamped entry to:
    logs/dfa_quality_audit.log

Each entry contains the full plain-text report (no colour codes) with a
run header so you can scroll back through history.
"""

import sys, json, os, re, textwrap
from collections import defaultdict
from datetime import datetime, timezone
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.schemas.bearing_signal import ValidationStatus, BearingSignalFact
from src.tools.data_loader import load_all_telemetry_rows, load_curated_signals
from src.tools.remediation import (
    remediate, imputable_fields, missing_critical_fields,
    decide_action, IMPUTE, DROP, KEEP,
)
from src.tools.curated_store import build_curated_rows, write_curated

# ── Colour helpers ────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
DIM    = "\033[2m"

def c(text, col):
    return col + str(text) + RESET

def score_col(s):
    if s >= 0.90: return GREEN
    if s >= 0.55: return YELLOW
    return RED

def bar(score, width=14):
    filled = round(score * width)
    return c("█" * filled, score_col(score)) + c("░" * (width - filled), DIM)

# ── ANSI stripper for log file ────────────────────────────────────────────────
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

def strip_ansi(text):
    """Remove all ANSI escape codes — used when writing to the log file."""
    return _ANSI_RE.sub("", text)

# ── Log writer ────────────────────────────────────────────────────────────────
LOG_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "dfa_quality_audit.log")

def append_to_log(plain_text: str, run_ts: str) -> str:
    """
    Appends one timestamped run block to the log file.
    Returns the log file path so main() can print it.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    header = (
        "\n" + "=" * 72 + "\n"
        + "  DFA QUALITY AUDIT RUN\n"
        + "  Timestamp : " + run_ts + "\n"
        + "=" * 72 + "\n"
    )
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(header)
        fh.write(plain_text)
        fh.write("\n")
    return LOG_FILE

ADVISORY = ("stale or from a replayed", "timestamp is", "record may be")

def real_failures(result):
    if not result.quality_report:
        return []
    return [
        d for d in result.quality_report.failed_dimensions
        if not any(kw in d.reason for kw in ADVISORY)
    ]


def build_report(results):
    asset_map = defaultdict(list)
    for r in results:
        asset_map[r.raw.asset_id].append(r)

    rows = []
    for asset_id, rlist in asset_map.items():
        scores    = [r.data_quality_score for r in rlist]
        avg_score = sum(scores) / len(scores)
        ctx       = next((r.asset_ctx for r in rlist if r.asset_ctx), None)
        name      = ctx.asset_name if ctx else "UNREGISTERED"

        # Collect unique real issues — deduplicated by fix action (same fix = same problem)
        seen_hint = {}   # hint → most recent reason
        for r in rlist:
            for d in real_failures(r):
                if d.hint:
                    seen_hint[d.hint] = d.reason   # keep latest reason for this fix type
                elif d.reason and d.reason not in seen_hint:
                    seen_hint[d.reason] = ""

        seen = {reason: hint for hint, reason in seen_hint.items()}

        # Unregistered asset — one clear root cause, not a cascade of failures
        if not ctx:
            issues = [(
                "asset_id '" + asset_id + "' is not registered in the asset master",
                "Register this asset in asset_master.json (Table 3), then add its "
                "bearings to bearing_master.json (Table 2)"
            )]

        elif not seen:
            # Only advisory issues — build a plain-English score explanation
            adv = []
            for r in rlist:
                if r.quality_report:
                    for d in r.quality_report.failed_dimensions:
                        if d.label not in adv:
                            adv.append(d.label)
            if adv:
                reason = (
                    "Score is " + str(round(avg_score * 100)) + "% — advisory issue only: "
                    + adv[0] + " (does not block routing)"
                )
            else:
                reason = "Score is 100% — all checks passed"
            issues = [(reason, "")]
        else:
            issues = list(seen.items())

        rows.append({
            "asset_id":   asset_id,
            "name":       name,
            "score":      avg_score,
            "records":    len(rlist),
            "registered": ctx is not None,
            "issues":     issues,
        })

    rows.sort(key=lambda x: (x["registered"], x["score"]))
    return rows


def print_report(rows, buf=None):
    """
    Prints the coloured report to stdout.
    If buf (a StringIO) is provided, also writes stripped plain text to it.
    """
    def out(line=""):
        print(line)
        if buf is not None:
            buf.write(strip_ansi(line) + "\n")

    out()
    out(c("  Data Foundation — Asset Quality Report", BOLD + CYAN))
    out(c("  " + "─" * 72, DIM))

    for a in rows:
        score = a["score"]
        sc    = score_col(score)
        pct   = str(round(score * 100)) + "%"
        recs  = "(" + str(a["records"]) + " records)"

        out()
        out("  " + c(a["asset_id"], BOLD) + "  " + c(a["name"], sc + BOLD)
            + "  " + bar(score) + " " + c(pct, sc) + "  " + c(recs, DIM))

        for reason, hint in a["issues"]:
            out("  " + c("Reason :", DIM) + " " + reason)
            if hint:
                out("  " + c("Fix    :", DIM) + " " + c(hint, CYAN))

    out()
    out(c("  " + "─" * 72, DIM))

    total  = len(rows)
    adv_ok = sum(1 for a in rows if not any(h for _, h in a["issues"]))
    real_p = sum(1 for a in rows if any(h for _, h in a["issues"]))
    unreg  = sum(1 for a in rows if not a["registered"])
    out(
        "  " + c(str(total), BOLD) + " assets  "
        + c(str(adv_ok), GREEN) + " advisory-only  "
        + c(str(real_p), YELLOW if real_p else GREEN) + " with real issues  "
        + c(str(unreg), RED) + " unregistered"
    )
    out()


def print_json(rows):
    out = [
        {
            "asset_id":   a["asset_id"],
            "asset_name": a["name"],
            "avg_score":  round(a["score"], 4),
            "records":    a["records"],
            "registered": a["registered"],
            "issues": [
                {"reason": r, "fix": h}
                for r, h in a["issues"]
            ],
        }
        for a in rows
    ]
    print(json.dumps(out, indent=2))


def run_remediation(agent, results, run_ts):
    """
    Interactive human-in-the-loop step. For every FLAGGED record that has at
    least one imputable missing field, ask the operator: impute, drop, or keep.
    This is the SAME decision a frontend button will make later — only the
    answerer differs.

    Outcomes are (1) persisted to the curated signals table and (2) appended
    to the audit log.
    """
    cfg = agent._cfg
    print(c("\n  Remediation — review FLAGGED records", BOLD + CYAN))
    print(c("  " + "─" * 72, DIM))

    candidates = [
        r for r in results
        if r.validation_status == ValidationStatus.FLAGGED
        and imputable_fields(r, cfg)
    ]

    decisions = {}   # telemetry_id -> final signal (or None if dropped)
    audit     = []   # plain-text audit lines for the log

    if not candidates:
        print(c("\n  No FLAGGED records with imputable fields to review.", DIM))
        audit.append("No FLAGGED records with imputable fields — no remediation performed.")

    for r in candidates:
        tid       = r.raw.telemetry_id
        missing   = missing_critical_fields(r, cfg)
        canfix    = imputable_fields(r, cfg)
        cannotfix = [f for f in missing if f not in canfix]
        policy    = decide_action(r, cfg)
        before    = r.data_quality_score

        print()
        print("  " + c(tid, BOLD) + "  "
              + c(r.raw.asset_id + "/" + r.raw.bearing_id, DIM)
              + "  score " + c(f"{before:.0%}", score_col(before)))
        print("  " + c("Missing    :", DIM) + " " + ", ".join(missing))
        print("  " + c("Imputable  :", DIM) + " " + c(", ".join(canfix) or "none", GREEN))
        if cannotfix:
            print("  " + c("No baseline:", DIM) + " " + c(", ".join(cannotfix), YELLOW)
                  + c("  (cannot impute — only drop/keep)", DIM))
        print("  " + c("Policy says:", DIM) + " " + policy)

        choice = input(c("  [i]mpute / [d]rop / [k]eep (enter = policy)> ", CYAN)).strip().lower()
        action = {"i": IMPUTE, "d": DROP, "k": KEEP, "": policy}.get(choice, policy)

        fixed = remediate(r, action, agent, cfg)

        if fixed is None:
            final = None
            print(c("  → DROPPED (removed from the pipeline)", RED))
            audit.append(f"{tid}  {r.raw.asset_id}/{r.raw.bearing_id}  DROPPED  (score {before:.0%})")
        elif fixed.remediation_action == IMPUTE:
            final = fixed
            print(c(f"  → IMPUTED {fixed.imputed_fields} via {fixed.imputation_method}", GREEN)
                  + "  new score " + c(f"{fixed.data_quality_score:.0%}",
                                       score_col(fixed.data_quality_score))
                  + c(f"  status {fixed.validation_status.value}", DIM))
            audit.append(
                f"{tid}  {r.raw.asset_id}/{r.raw.bearing_id}  IMPUTED {fixed.imputed_fields} "
                f"via {fixed.imputation_method}  {before:.0%} -> {fixed.data_quality_score:.0%}  "
                f"status {fixed.validation_status.value}"
            )

            # Two-step: imputation filled what it could, but if critical fields
            # remain missing (no baseline to impute from), the record is still
            # incomplete — ask whether to keep it flagged or drop it.
            still = missing_critical_fields(fixed, cfg)
            if still:
                print("  " + c("Still missing:", DIM) + " " + c(", ".join(still), YELLOW)
                      + c("  (no baseline — cannot impute)", DIM))
                choice2 = input(c("    record still incomplete — [k]eep / [d]rop> ", CYAN)).strip().lower()
                action2 = {"k": KEEP, "d": DROP, "": KEEP}.get(choice2, KEEP)
                final   = remediate(fixed, action2, agent, cfg)
                if final is None:
                    print(c("    → DROPPED (still-incomplete record removed)", RED))
                    audit.append(f"    └─ then DROPPED (still missing {still})")
                else:
                    print(c(f"    → KEPT (flows downstream flagged; still missing {still})", YELLOW))
                    audit.append(f"    └─ then KEPT (still missing {still})")
        else:
            final = fixed
            print(c("  → KEPT as-is (flows downstream with its quality flag)", YELLOW))
            audit.append(f"{tid}  {r.raw.asset_id}/{r.raw.bearing_id}  KEPT  (score {before:.0%})")

        decisions[tid] = final

    # ── Persist curated table + audit log ────────────────────────────
    flat_rows    = build_curated_rows(results, decisions)
    curated_path = write_curated(flat_rows)

    dropped = sum(1 for v in decisions.values() if v is None)
    imputed = sum(1 for v in decisions.values()
                  if v is not None and v.remediation_action == IMPUTE)

    print()
    print(c("  " + "─" * 72, DIM))
    print("  " + c(str(len(flat_rows)), BOLD) + " routable records written to curated table  "
          + c(str(imputed), GREEN) + " imputed  "
          + c(str(dropped), RED) + " dropped")
    print(c("  Curated table → " + curated_path, DIM))

    audit_text = (
        "REMEDIATION SESSION\n"
        + "\n".join("  " + line for line in audit)
        + f"\n\n  Curated table written: {len(flat_rows)} routable records "
        + f"({imputed} imputed, {dropped} dropped) → {curated_path}"
    )
    log_path = append_to_log(audit_text, run_ts)
    print(c("  Audit log    → " + log_path, DIM))
    print()

    # ── Collect routable TrustedBearingSignal objects for downstream ──
    # This is the in-memory list that Monitoring (and later agents) consume.
    # It mirrors exactly what was written to the curated table — no re-read
    # needed because we already have the objects.
    routable = []
    for r in results:
        tid = r.raw.telemetry_id
        if tid in decisions:
            final = decisions[tid]
            if final is not None:
                routable.append(final)       # remediated signal
        elif r.validation_status != ValidationStatus.REJECTED:
            routable.append(r)               # passed through as-is
    return routable


def _anomaly_status(worst_score):
    """Return (label, colour) based on the worst anomaly score for an asset."""
    if worst_score >= 0.95:
        return "CRITICAL", RED
    if worst_score >= 0.85:
        return "WARNING",  YELLOW
    return "HEALTHY", GREEN


def run_monitoring(routable):
    """
    Run the Monitoring Agent on the curated (remediated) signal list and
    print a per-asset anomaly report.

    Layout per asset
    ────────────────
    ASSET_ID  Asset Name  ████  score%  STATUS  (criticality)
      └ BRG_ID  position  N anomalies  worst score%  T²=xx  signals↑

    Status levels
    ─────────────
    CRITICAL  score >= 95%  — immediate attention required
    WARNING   score >= 85%  — anomaly threshold crossed, monitor closely
    HEALTHY   no anomalies detected
    """
    from collections import defaultdict

    mon = MonitoringAgent.from_config()

    # ── Run monitoring; index results by asset and bearing ───────────
    asset_info   = {}
    bearing_info = {}
    events_by    = defaultdict(lambda: defaultdict(list))

    for t in routable:
        if t.asset_ctx:
            asset_info[t.raw.asset_id] = t.asset_ctx
        if t.bearing_ctx:
            bearing_info[t.raw.bearing_id] = (
                t.bearing_ctx.bearing_position, t.raw.asset_id
            )
        ev = mon.process(t)
        if ev:
            events_by[t.raw.asset_id][t.raw.bearing_id].append(ev)

    if not asset_info:
        print(c("\n  No routable records to assess.\n", DIM))
        return

    # ── Sort assets: worst score first, healthy last ─────────────────
    def worst_score(aid):
        return max(
            (e.anomaly_score
             for bd in events_by.get(aid, {}).values()
             for e in bd),
            default=0.0,
        )

    sorted_assets = sorted(asset_info.keys(), key=worst_score, reverse=True)

    print(c("\n  Monitoring — Asset Anomaly Report  (Hotelling T²)", BOLD + CYAN))
    print(c("  " + "─" * 72, DIM))

    n_critical = n_warning = n_healthy = 0
    total_events = sum(
        len(evs)
        for bd in events_by.values()
        for evs in bd.values()
    )

    _short = {"vib_rms_mm_s": "vib", "kurtosis": "kurt",
              "temp_c": "temp", "bpfo_energy": "bpfo"}

    for asset_id in sorted_assets:
        ctx           = asset_info[asset_id]
        w             = worst_score(asset_id)
        status, sc    = _anomaly_status(w)
        crit_tag      = ctx.criticality.upper()
        bottleneck    = " · bottleneck" if ctx.is_bottleneck else ""
        cost_tag      = f" · ${ctx.downtime_cost_per_hour:,}/hr" if ctx.is_bottleneck else ""

        if   status == "CRITICAL": n_critical += 1
        elif status == "WARNING":  n_warning  += 1
        else:                      n_healthy  += 1

        # ── Asset header ──────────────────────────────────────────────
        print()
        print(
            "  " + c(asset_id, BOLD)
            + "  " + c(ctx.asset_name, sc + BOLD)
            + "  " + bar(w)
            + " " + c(f"{w:.0%}", sc + BOLD)
            + "  " + c(status, sc + BOLD)
            + c(f"  ({crit_tag}{bottleneck}{cost_tag})", DIM)
        )

        bearing_events = events_by.get(asset_id, {})

        if not bearing_events:
            print(c("    No anomalies detected across all readings", DIM))
            continue

        # ── Bearing rows ──────────────────────────────────────────────
        sorted_bearings = sorted(
            bearing_events.items(),
            key=lambda kv: max(e.anomaly_score for e in kv[1]),
            reverse=True,
        )

        for brg_id, evs in sorted_bearings:
            position = bearing_info.get(brg_id, ("?", None))[0]
            worst_ev = max(evs, key=lambda e: e.anomaly_score)
            t2_val   = worst_ev.evidence.get("t2_statistic", 0.0)
            p_val    = worst_ev.evidence.get("t2_degrees_freedom", 4)
            trig     = worst_ev.triggered_features
            conf     = worst_ev.confidence_score
            _, bsc   = _anomaly_status(worst_ev.anomaly_score)
            n_str    = f"{len(evs)} anomal" + ("y" if len(evs) == 1 else "ies")

            # ── Bearing summary line ───────────────────────────────────
            print(
                "    " + c("└", DIM)
                + " " + c(brg_id, BOLD)
                + c(f"  {position:<10}", DIM)
                + "  " + c(n_str, bsc)
                + "  ·  worst " + c(f"{worst_ev.anomaly_score:.0%}", bsc + BOLD)
                + "  ·  T²=" + c(f"{t2_val:.1f}", DIM)
                + f" (p={p_val})"
                + "  ·  conf " + c(f"{conf:.0%}",
                                    GREEN if conf >= 0.95 else YELLOW)
            )

            # ── Confidence breakdown ───────────────────────────────────
            conf_detail = worst_ev.evidence
            dq  = conf_detail.get("data_quality_score", 0)
            exc = conf_detail.get("excluded_imputed", [])
            print(c(
                f"           Confidence {conf:.0%} — based on: "
                f"sensor quality · signal coverage ({p_val}/4 signals scored)"
                f" · data quality score ({dq:.0%})"
                + (f"  ·  excluded (imputed): {', '.join(exc)}" if exc else ""),
                DIM,
            ))

            # ── Why flagged (plain-English, for non-experts) ────────────
            # Use the AnomalyEvent.reason built by the Monitoring Agent; fall
            # back to a terse signal list only if it's somehow empty.
            if worst_ev.reason:
                reason_text = worst_ev.reason
            elif trig:
                reason_text = (", ".join(_short.get(f, f) for f in trig)
                               + " deviated beyond the trigger threshold while running.")
            else:
                reason_text = ("All signals shifted together (joint T² deviation) even though "
                               "none individually crossed the trigger threshold.")
            print(c("           Why flagged:", BOLD))
            for line in textwrap.wrap(reason_text, width=78):
                print("             " + line)

            # ── Per-signal evidence table ─────────────────────────────
            signals = worst_ev.evidence.get("signals", {})
            if signals:
                ts_short = worst_ev.timestamp_utc.replace("T", " ").replace("Z", " UTC")
                print(c(
                    f"           Evidence from worst reading  ({ts_short}):",
                    DIM,
                ))
                print(c(
                    f"           {'Signal':<10}  {'Actual':>8}  "
                    f"{'Baseline':>16}  {'z-score':>9}  Severity",
                    DIM,
                ))
                for sig_name, sig_data in signals.items():
                    sname  = _short.get(sig_name, sig_name)
                    val    = sig_data.get("value", 0)
                    mean   = sig_data.get("mean",  0)
                    std    = sig_data.get("std",   1)
                    z      = sig_data.get("z",     0)
                    # Colour z-score: >=3σ red, >=2σ yellow, else dim
                    if abs(z) >= 3:
                        zcol = RED
                    elif abs(z) >= 2:
                        zcol = YELLOW
                    else:
                        zcol = DIM
                    # Severity bar: maps |z|/20 → bar width 10
                    z_norm  = min(1.0, abs(z) / 20.0)
                    z_fill  = round(z_norm * 10)
                    z_bar   = c("█" * z_fill, zcol) + c("░" * (10 - z_fill), DIM)
                    # Direction arrow
                    arrow   = "↑" if z > 0 else ("↓" if z < 0 else "")
                    print(
                        c(f"           {sname:<10}  {val:>8.2f}  "
                          f"{mean:>7.2f} ± {std:<6.2f}  ", DIM)
                        + c(f"z={z:>+6.1f}σ {arrow}", zcol + BOLD)
                        + "  " + z_bar
                    )
            print()

    # ── Footer ───────────────────────────────────────────────────────
    print()
    print(c("  " + "─" * 72, DIM))
    print(
        "  " + c(str(len(sorted_assets)), BOLD) + " assets  "
        + c(str(n_critical), RED)    + " critical  "
        + c(str(n_warning),  YELLOW) + " warning  "
        + c(str(n_healthy),  GREEN)  + " healthy  "
        + c(f"│  {total_events} anomal"
            + ("y" if total_events == 1 else "ies")
            + f" across {len(routable)} records", DIM)
    )
    print()


def main():
    json_mode      = "--json" in sys.argv
    remediate_mode = "--remediate" in sys.argv
    run_ts         = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    rows_data = load_all_telemetry_rows()
    agent     = DataFoundationAgent.from_data_files()
    results   = agent.process_batch(rows_data)
    report    = build_report(results)

    if remediate_mode:
        # Full pipeline:
        # 1. DFA already ran above (results)
        # 2. Human reviews FLAGGED records → decisions persisted to curated table
        # 3. Monitoring runs on the cleaned, routable signal list
        routable = run_remediation(agent, results, run_ts)
        run_monitoring(routable)
        return

    if json_mode:
        # JSON mode: print to stdout and save .log as JSON lines
        print_json(report)
        os.makedirs(LOG_DIR, exist_ok=True)
        json_text = json.dumps({"run_ts": run_ts, "assets": [
            {
                "asset_id":   a["asset_id"],
                "asset_name": a["name"],
                "avg_score":  round(a["score"], 4),
                "records":    a["records"],
                "issues":     [{"reason": r, "fix": h} for r, h in a["issues"]],
            }
            for a in report
        ]}, indent=2)
        append_to_log(json_text, run_ts)
        print(c("\n  Log saved → " + LOG_FILE, DIM), file=sys.stderr)
    else:
        # Plain mode: print coloured to terminal, capture plain text for log
        buf = StringIO()
        header_line = (
            "\n  " + str(len(rows_data)) + " records across "
            + str(len(report)) + " assets"
        )
        print(c(header_line, DIM))
        buf.write(strip_ansi(header_line) + "\n")

        print_report(report, buf=buf)

        log_path = append_to_log(buf.getvalue(), run_ts)
        print(c("  Log saved → " + log_path, DIM))


if __name__ == "__main__":
    main()