"""
agents/monitoring_agent.py  —  Phase 3

Monitoring Agent — Hotelling T² anomaly detection.

Entry point:  MonitoringAgent.process(trusted) -> Optional[AnomalyEvent]

Primary algorithm: Hotelling T²
  T² = (x − μ)ᵀ  Σ⁻¹  (x − μ)

  where x is the current signal vector, μ is the per-bearing baseline mean
  vector, and Σ is the baseline covariance matrix (reconstructed from the
  stds and pairwise correlations stored in bearing_master).

  T² is normalised to a 0–1 anomaly score via the chi-square CDF with p
  degrees of freedom (p = number of usable signals).

Why T² over individual z-scores?
  A bearing fault causes vib, kurtosis, temp, and bpfo to rise together in
  a correlated pattern. T² treats the four signals as a vector and accounts
  for their baseline correlation — it flags the joint deviation rather than
  scoring each signal independently. This catches:
    * Correlated rises that are individually borderline but jointly anomalous.
    * Unusual deviation patterns (e.g. temp rising faster than vib) invisible
      to per-signal z-scores.

Z-scores are retained for evidence (triggered_features, per-signal breakdown).

Reduced T²:
  When some signals are null or were imputed (and excluded), T² is computed
  on the available sub-matrix rather than the full 4×4 matrix.  chi-square
  degrees of freedom p adjusts accordingly.

Pipeline:
  1. Suppress startup/shutdown records.
  2. Classify operating regime; suppress non-running.
  3. Collect usable signals (non-null, not imputed if excluded).
  4. Build covariance sub-matrix and invert it.
  5. Compute T² and normalise to 0–1 via chi-square CDF.
  6. Compute z-scores for evidence.
  7. Compute confidence (sensor fidelity + coverage + data quality).
  8. Emit AnomalyEvent if score >= threshold, else return None.
"""
from __future__ import annotations

import logging
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional, List

import numpy as np

from src.schemas.bearing_signal import TrustedBearingSignal
from src.schemas.anomaly import AnomalyEvent, MonitoringResult
from src.tools.baseline_features import (
    build_correlation_matrix,
    build_covariance_matrix,
    invert_covariance,
    diagonal_inverse,
    compute_hotelling_t2,
    t2_anomaly_score,
    compute_z_score,
    classify_regime,
    compute_confidence,
    update_ewma,
    ewma_z,
    ewma_anomaly_score,
    build_anomaly_reason,
)
from src.tools.config_loader import MonitoringConfig, load_monitoring_config
from src.tools.ewma_store import mutate_state

logger = logging.getLogger(__name__)


class MonitoringAgent:
    """Stateless agent. Config injected at construction."""

    def __init__(self, cfg: MonitoringConfig = None, decision_repository=None, now_fn=None):
        self._cfg = cfg or load_monitoring_config()
        # ************** Added by Prateek Mittal on 17th July 2026 ******************
        # Agent 2 provenance, auditable decision persistence, and deterministic clock.
        self._decision_repository = decision_repository
        self._now_fn = now_fn or (lambda: datetime.now(tz=timezone.utc))
        encoded = json.dumps(asdict(self._cfg), sort_keys=True, default=str).encode("utf-8")
        self._config_version = hashlib.sha256(encoded).hexdigest()[:16]
        # ***********************

    @classmethod
    def from_config(cls) -> "MonitoringAgent":
        return cls(load_monitoring_config())

    # ************** Added by Prateek Mittal on 17th July 2026 ******************
    # Convenience factory for auditable local/pilot monitoring decisions.
    @classmethod
    def with_sqlite_repository(cls, path: str = None) -> "MonitoringAgent":
        from src.tools.monitoring_decision_repository import SQLiteMonitoringDecisionRepository
        cfg = load_monitoring_config()
        repository = SQLiteMonitoringDecisionRepository(path or cfg.decision_repository_path)
        return cls(cfg, decision_repository=repository)
    # ***********************

    # ------------------------------------------------------------------

    # ************** Added by Prateek Mittal on 17th July 2026 ******************
    # Detailed assessment API. It preserves every non-anomaly outcome instead
    # of collapsing healthy, suppressed, and unassessable inputs into `None`.
    def assess(self, trusted: TrustedBearingSignal) -> MonitoringResult:
        raw = trusted.raw
        now = self._now_fn().isoformat()
        base = dict(
            telemetry_id=raw.telemetry_id, asset_id=raw.asset_id,
            bearing_id=raw.bearing_id, timestamp_utc=raw.timestamp_utc,
            processed_at=now, monitoring_config_version=self._config_version,
            source_schema_version=getattr(trusted, "schema_version", ""),
            source_config_version=getattr(trusted, "config_version", ""),
            source_master_data_version=getattr(trusted, "master_data_version", ""),
        )

        # Duplicate and late-event identities are more specific than the
        # general Agent 1 ineligible route and are reported first.
        if getattr(trusted, "duplicate_detected", False):
            return MonitoringResult(status="duplicate", suppression_reason="duplicate event cannot update monitoring state", **base)
        if getattr(trusted, "out_of_order", False):
            return MonitoringResult(status="out_of_order", suppression_reason="late event cannot update monitoring state", **base)
        if not getattr(trusted, "downstream_eligible", False):
            return MonitoringResult(status="ineligible", suppression_reason=(
                getattr(trusted, "routing_reason", "") or "Agent 1 marked input ineligible"
            ), **base)
        if raw.startup_shutdown_flag:
            return MonitoringResult(status="suppressed", suppression_reason="startup_shutdown", **base)
        if trusted.bearing_ctx is None:
            return MonitoringResult(status="insufficient_data", suppression_reason="missing_bearing_context", **base)

        rated_rpm = trusted.asset_ctx.rated_rpm if trusted.asset_ctx else None
        regime = classify_regime(raw.rpm, raw.load_pct, raw.machine_state, rated_rpm, self._cfg)
        if not regime["running"]:
            return MonitoringResult(status="suppressed", suppression_reason="non_running_regime", regime=regime["label"], **base)

        imputed = set(trusted.imputed_fields) if self._cfg.exclude_imputed_fields else set()
        usable, excluded = [], []
        for field in self._cfg.signal_order:
            attrs = self._cfg.signal_baselines.get(field, {})
            value = getattr(raw, field, None)
            mean = getattr(trusted.bearing_ctx, attrs.get("mean", ""), None)
            std = getattr(trusted.bearing_ctx, attrs.get("std", ""), None)
            if field in imputed:
                excluded.append(field)
            elif value is not None and mean is not None and std not in (None, 0):
                usable.append(field)
        if len(usable) < self._cfg.min_signals_for_t2:
            return MonitoringResult(
                status="insufficient_data", suppression_reason="insufficient_usable_signals",
                regime=regime["label"], usable_signals=usable,
                excluded_signals=excluded, **base,
            )

        event, state_recovered, detector_status = self._detect(trusted)
        status = detector_status
        return MonitoringResult(
            status=status, assessed=True, anomaly_event=event,
            regime=regime["label"], usable_signals=usable,
            excluded_signals=excluded, state_recovered=state_recovered, **base,
        )

    def assess_and_store(self, trusted: TrustedBearingSignal) -> MonitoringResult:
        if self._decision_repository is None:
            raise RuntimeError("assess_and_store requires a monitoring decision repository")
        result = self.assess(trusted)
        try:
            result.persistence_status = "stored"
            self._decision_repository.save(result)
        except Exception as exc:
            result.persistence_status = "failed"
            result.suppression_reason = f"decision persistence failed: {exc}"
        return result

    def process(self, trusted: TrustedBearingSignal) -> Optional[AnomalyEvent]:
        """Compatibility API: return only an emitted anomaly event."""
        return self.assess(trusted).anomaly_event
    # ***********************

    def _detect(self, trusted: TrustedBearingSignal) -> tuple[Optional[AnomalyEvent], bool, str]:
        """Assess one trusted signal. Returns an AnomalyEvent or None."""
        cfg  = self._cfg
        raw  = trusted.raw
        bctx = trusted.bearing_ctx

        # ── Step 1: startup/shutdown suppression ────────────────────────
        if raw.startup_shutdown_flag:
            return None, False, "suppressed"

        if bctx is None:
            return None, False, "insufficient_data"

        # ── Step 2: regime ──────────────────────────────────────────────
        rated_rpm = trusted.asset_ctx.rated_rpm if trusted.asset_ctx else None
        regime = classify_regime(
            raw.rpm, raw.load_pct, raw.machine_state, rated_rpm, cfg
        )
        if not regime["running"]:
            return None, False, "suppressed"

        # ── Step 3: collect usable signals ──────────────────────────────
        imputed      = set(trusted.imputed_fields) if cfg.exclude_imputed_fields else set()
        avail_names  = []   # signal field names available for T²
        avail_idx    = []   # their indices in signal_order (for submatrix)
        x_vals       = []   # current reading values
        mu_vals      = []   # baseline means
        std_vals     = []   # baseline stds
        excluded     = []   # imputed/missing fields skipped

        for idx, field in enumerate(cfg.signal_order):
            value = getattr(raw, field, None)
            attrs = cfg.signal_baselines.get(field, {})
            mean  = getattr(bctx, attrs.get("mean", ""), None)
            std   = getattr(bctx, attrs.get("std",  ""), None)

            if value is None or mean is None or std is None or std == 0:
                continue
            if field in imputed:
                excluded.append(field)
                continue

            avail_names.append(field)
            avail_idx.append(idx)
            x_vals.append(value)
            mu_vals.append(mean)
            std_vals.append(std)

        p = len(avail_names)
        if p < cfg.min_signals_for_t2:
            return None, False, "insufficient_data"

        # ── Step 4: build covariance sub-matrix and invert ──────────────
        full_corr = build_correlation_matrix(bctx)
        corr_sub  = full_corr[np.ix_(avail_idx, avail_idx)]
        cov_sub   = build_covariance_matrix(std_vals, corr_sub)

        # ************** Added by Prateek Mittal on 17th July 2026 ******************
        # Preserve whether diagonal fallback was used; checking `cov_inv is None`
        # after replacement previously made the evidence always report false.
        cov_inv = invert_covariance(cov_sub)
        cov_inv_fallback = cov_inv is None
        if cov_inv is None:
            # Singular matrix — fall back to diagonal (independent z-scores)
            cov_inv = diagonal_inverse(std_vals)
        # ***********************

        # ── Step 5: T² and anomaly score ────────────────────────────────
        x  = np.array(x_vals)
        mu = np.array(mu_vals)

        t2            = compute_hotelling_t2(x, mu, cov_inv)
        anomaly_score = t2_anomaly_score(t2, p)

        # ── Step 6: z-scores for evidence ───────────────────────────────
        z_scores   = {
            name: round(compute_z_score(xv, mv, sv), 4)
            for name, xv, mv, sv in zip(avail_names, x_vals, mu_vals, std_vals)
        }
        triggered  = [f for f, z in z_scores.items() if abs(z) >= cfg.trigger_z]
        primary_z  = max((abs(z) for z in z_scores.values()), default=0.0)
        per_signal = {
            name: {
                "value": xv, "mean": mv, "std": sv,
                "z": z_scores[name],
            }
            for name, xv, mv, sv in zip(avail_names, x_vals, mu_vals, std_vals)
        }

        # ── Step 7: confidence ──────────────────────────────────────────
        coverage   = p / len(cfg.signal_order)
        confidence = compute_confidence(
            signal_quality = raw.signal_quality_score,
            coverage       = coverage,
            data_quality   = trusted.data_quality_score,
            weights        = cfg.confidence_weights,
        )

        # ── Step 7.5: EWMA control chart (parallel to T²) ───────────────
        ewma_fired      = False
        ewma_score      = 0.0
        ewma_triggered  = []
        ewma_per_signal = {}
        state_recovered = False

        if cfg.ewma_enabled and cfg.ewma_signals:
            # ************** Added by Prateek Mittal on 17th July 2026 ******************
            # One atomic mutation isolates EWMA state by configuration/baseline,
            # bearing, and operating regime and reports corrupt-state recovery.
            state_version = (
                f"{cfg.state_namespace_version}:{self._config_version}:"
                f"{getattr(trusted, 'master_data_version', '')}"
            )
            imputed_set = set(trusted.imputed_fields) if cfg.ewma_exclude_imputed else set()

            def update_state(state):
                namespace = state.setdefault("namespaces", {}).setdefault(state_version, {})
                bearing_state = namespace.setdefault(raw.bearing_id, {}).setdefault(regime["label"], {})
                details, triggered_fields = {}, []
                for field in cfg.ewma_signals:
                    value = getattr(raw, field, None)
                    attrs = cfg.signal_baselines.get(field, {})
                    mean = getattr(bctx, attrs.get("mean", ""), None)
                    std = getattr(bctx, attrs.get("std", ""), None)
                    if value is None or mean is None or std in (None, 0) or field in imputed_set:
                        continue
                    prev = bearing_state.get(field, mean)
                    new_ewma = update_ewma(prev, value, cfg.ewma_alpha)
                    z = ewma_z(new_ewma, mean, std, cfg.ewma_alpha)
                    bearing_state[field] = new_ewma
                    details[field] = {
                        "value": value, "ewma": round(new_ewma, 4),
                        "mean": mean, "std": std, "z": round(z, 4),
                    }
                    if abs(z) >= cfg.ewma_control_limit:
                        triggered_fields.append(field)
                return details, triggered_fields

            (ewma_per_signal, ewma_triggered), state_recovered = mutate_state(
                cfg.ewma_state_file, update_state
            )
            # ***********************

            if ewma_triggered:
                ewma_fired = True
                max_abs_z  = max(abs(d["z"]) for d in ewma_per_signal.values())
                ewma_score = ewma_anomaly_score(
                    max_abs_z, cfg.ewma_control_limit, cfg.anomaly_threshold
                )

        processed_at = self._now_fn().isoformat()

        # ── Step 8: verdict (T² OR EWMA can fire) ───────────────────────
        t2_fired = anomaly_score >= cfg.anomaly_threshold

        if not t2_fired and not ewma_fired:
            _, _, alert_recovered = self._apply_alert_policy(
                trusted, regime["label"], raw_anomaly=False
            )
            state_recovered = state_recovered or alert_recovered
            if cfg.log_healthy:
                logger.info("HEALTHY [%s] %s/%s T²=%.2f score=%.3f",
                            raw.telemetry_id, raw.asset_id, raw.bearing_id,
                            t2, anomaly_score)
            return None, state_recovered, "healthy"

        # ************** Added by Prateek Mittal on 17th July 2026 ******************
        # Apply configurable persistence/debounce policy after detector firing.
        alert_allowed, alert_status, alert_recovered = self._apply_alert_policy(
            trusted, regime["label"], raw_anomaly=True
        )
        state_recovered = state_recovered or alert_recovered
        if not alert_allowed:
            return None, state_recovered, alert_status
        # ***********************

        triggered_methods = []
        if t2_fired:   triggered_methods.append("hotelling_t2")
        if ewma_fired: triggered_methods.append("ewma")

        # Combined anomaly score so downstream code can still sort by severity
        combined_score = max(anomaly_score, ewma_score)
        combined_triggered = list(dict.fromkeys(triggered + ewma_triggered))

        # Business-readable "why it was flagged" (deterministic, no LLM).
        reason = build_anomaly_reason(
            z_scores       = z_scores,
            t2_fired       = t2_fired,
            ewma_triggered = ewma_triggered,
            regime_label   = regime["label"],
            confidence     = confidence,
        )

        event = AnomalyEvent(
            case_id            = f"ANOM-{raw.bearing_id}-{raw.timestamp_utc}",
            asset_id           = raw.asset_id,
            bearing_id         = raw.bearing_id,
            channel_id         = raw.channel_id,
            timestamp_utc      = raw.timestamp_utc,
            anomaly_score      = combined_score,
            confidence_score   = confidence,
            z_score            = round(primary_z, 4),
            triggered_features = combined_triggered,
            reason             = reason,
            regime             = regime["label"],
            evidence           = {
                "method":             "hotelling_t2",
                "triggered_methods":  triggered_methods,
                "t2_statistic":       round(t2, 4),
                "t2_degrees_freedom": p,
                "t2_anomaly_score":   round(anomaly_score, 4),
                "t2_fired":           t2_fired,
                "signals":            per_signal,
                "regime":             regime,
                "excluded_imputed":   excluded,
                "data_quality_score": trusted.data_quality_score,
                "validation_status":  trusted.validation_status.value,
                "cov_inv_fallback":   cov_inv_fallback,
                "state_recovered":    state_recovered,
                "ewma": {
                    "fired":              ewma_fired,
                    "score":              round(ewma_score, 4),
                    "alpha":              cfg.ewma_alpha,
                    "control_limit_L":    cfg.ewma_control_limit,
                    "triggered_features": ewma_triggered,
                    "signals":            ewma_per_signal,
                },
            },
            baseline_ref = raw.bearing_id,
            processed_at = processed_at,
            # ************** Added by Prateek Mittal on 17th July 2026 ******************
            # Link every anomaly to the effective detector and Agent 1 provenance.
            monitoring_config_version = self._config_version,
            source_schema_version      = getattr(trusted, "schema_version", ""),
            source_config_version      = getattr(trusted, "config_version", ""),
            source_master_data_version = getattr(trusted, "master_data_version", ""),
            # ***********************
        )

        if cfg.log_anomalies:
            logger.info(
                "ANOMALY [%s] %s/%s methods=%s T²=%.2f t2_score=%.3f ewma_score=%.3f conf=%.2f triggered=%s",
                raw.telemetry_id, raw.asset_id, raw.bearing_id,
                triggered_methods, t2, anomaly_score, ewma_score,
                confidence, combined_triggered,
            )
        return event, state_recovered, "anomaly"

    # ************** Added by Prateek Mittal on 17th July 2026 ******************
    # Lightweight alert lifecycle policy. Detector evidence remains untouched;
    # emission may wait for persistence or cooldown to avoid repeated alerts.
    def _apply_alert_policy(self, trusted, regime_label: str, raw_anomaly: bool):
        cfg = self._cfg
        now_epoch = self._now_fn().timestamp()
        state_version = (
            f"{cfg.state_namespace_version}:{self._config_version}:"
            f"{getattr(trusted, 'master_data_version', '')}"
        )

        def update(state):
            alerts = state.setdefault("alert_policy", {}).setdefault(state_version, {})
            slot = alerts.setdefault(trusted.raw.bearing_id, {}).setdefault(
                regime_label, {"consecutive": 0, "last_emitted_epoch": None}
            )
            if not raw_anomaly:
                slot["consecutive"] = 0
                return True, "healthy"
            slot["consecutive"] = int(slot.get("consecutive", 0)) + 1
            if slot["consecutive"] < cfg.min_consecutive_anomalies:
                return False, "pending_alert"
            last = slot.get("last_emitted_epoch")
            if last is not None and now_epoch - float(last) < cfg.alert_cooldown_seconds:
                return False, "cooldown"
            slot["last_emitted_epoch"] = now_epoch
            slot["consecutive"] = 0
            return True, "anomaly"

        (allowed, status), recovered = mutate_state(cfg.ewma_state_file, update)
        return allowed, status, recovered
    # ***********************

    # ------------------------------------------------------------------

    def process_batch(self, trusted_list: list) -> list:
        """Return only the AnomalyEvents (healthy → None, dropped from output)."""
        return [e for e in (self.process(t) for t in trusted_list) if e is not None]
