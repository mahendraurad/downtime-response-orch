"""
agents/data_foundation_agent.py

Data Foundation Agent — Phase 2

Entry point:  DataFoundationAgent.process(raw_dict) -> TrustedBearingSignal

All thresholds, weights, and limits are read from config/dfa_config.json.
No magic numbers live in this file.

Pipeline:
  1.  Parse raw dict into BearingSignalFact
  2.  Asset mapping check       (Integrity — REJECTED on failure)
  3.  Bearing + channel mapping (Integrity — REJECTED on failure)
  4.  Value range checks        (Validity / Conformity)
  5.  Accuracy check            (historian signal-quality / sensor fidelity)
  6.  Consistency check         (cross-field logical agreement)
  7.  Completeness check
  8.  Join AssetContext and BearingContext
  9.  Compute composite data_quality_score across the six dimensions
  10. Assign ValidationStatus: VALID | FLAGGED | REJECTED
  11. Build QualityReport with per-dimension scores and improvement hints
  12. Return TrustedBearingSignal
"""

from __future__ import annotations

import logging
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, Any

from src.schemas.bearing_signal import (
    BearingSignalFact,
    TrustedBearingSignal,
    ValidationDetail,
    ValidationStatus,
    QualityReport,
    QualityDimension,
)
from src.tools.validators import (
    validate_asset_id,
    validate_bearing_id,
    validate_ranges,
    compute_accuracy_score,
    check_consistency,
    compute_completeness_score,
    compute_data_quality_score,
)
from src.tools.enrichment import build_asset_context, build_bearing_context
from src.tools.config_loader import DFAConfig, load_config
# ************** Added by Prateek Mittal on 16th July 2026 ******************
# New deterministic Agent 1 utilities used for normalization, freshness,
# complete identity validation, and downstream routing.
from src.tools.data_foundation_utilities import (
    evaluate_routing,
    normalize_record,
    validate_asset_bearing_relationship,
    validate_timestamp,
)
# ***********************

logger = logging.getLogger(__name__)


class DataFoundationAgent:
    """
    Stateless agent. Config and lookups are injected at construction time.

    Usage:
        agent = DataFoundationAgent.from_data_files()
        result: TrustedBearingSignal = agent.process(raw_dict)

        # Inspect quality report
        for dim in result.quality_report.dimensions:
            print(dim.name, dim.score, dim.reason)
    """

    def __init__(
        self,
        asset_lookup:   Dict,
        bearing_lookup: Dict,
        channel_lookup: Dict,
        cfg:            DFAConfig = None,
        now_fn=None,
        # ************** Added by Prateek Mittal on 16th July 2026 ******************
        # Optional durable repository enables idempotency and ordering checks.
        repository=None,
        # ***********************
    ):
        self._assets   = asset_lookup
        self._bearings = bearing_lookup
        self._channels = channel_lookup
        self._cfg      = cfg or load_config()
        self._now_fn   = now_fn or (lambda: datetime.now(tz=timezone.utc))
        # ************** Added by Prateek Mittal on 16th July 2026 ******************
        # Stable fingerprints make every Agent 1 decision reproducible.
        self._repository = repository
        self._config_version = self._hash_payload(asdict(self._cfg))
        self._master_data_version = self._hash_payload({
            "assets": {key: value.model_dump() for key, value in sorted(self._assets.items())},
            "bearings": {key: value.model_dump() for key, value in sorted(self._bearings.items())},
        })
        # ***********************

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def process(self, raw: Dict[str, Any], persona_context=None) -> TrustedBearingSignal:
        """
        Process one raw telemetry record. Never raises — all errors are
        captured in the returned TrustedBearingSignal.
        """
        cfg          = self._cfg
        now          = self._now_fn()
        processed_at = now.isoformat()
        validation   = ValidationDetail()

        # ── Step 1: Parse ───────────────────────────────────────────────
        if not isinstance(raw, dict):
            return self._reject(
                raw, validation, processed_at,
                f"Input must be a dict, got {type(raw).__name__}"
            )
        # ************** Added by Prateek Mittal on 16th July 2026 ******************
        # Normalize source-specific payloads before enforcing the canonical schema,
        # then validate timestamp syntax and live-event freshness.
        source_profile = str(raw.get("data_source", "default")).lower()
        profile = cfg.source_profiles.get(source_profile, {})
        aliases = {**cfg.field_aliases, **profile.get("field_aliases", {})}
        conversions = {**cfg.unit_conversions, **profile.get("unit_conversions", {})}
        normalized = normalize_record(
            raw, aliases, conversions,
            set(BearingSignalFact.model_fields.keys()),
        )
        try:
            fact = BearingSignalFact.from_dict(normalized.record)
        except (ValueError, TypeError) as e:
            return self._reject(raw, validation, processed_at,
                                f"Failed to parse record: {e}",
                                normalization_actions=normalized.actions,
                                unknown_fields=normalized.unknown_fields)

        signal_dict = fact.to_dict()

        freshness = validate_timestamp(
            fact.timestamp_utc, fact.data_source, now,
            cfg.require_timestamp_timezone, cfg.max_future_seconds,
            cfg.max_age_seconds, cfg.live_sources,
        )
        validation.freshness_valid = freshness.valid
        if freshness.reasons:
            validation.reasons.extend(freshness.reasons)
        # ***********************

        # ── Step 2: Asset mapping  (HARD REJECT) ────────────────────────
        asset_ok, asset_reason = validate_asset_id(fact.asset_id, self._assets)
        validation.asset_mapping = asset_ok
        if not asset_ok:
            validation.reasons.append(asset_reason)
            report = self._build_report(
                asset_mapped=False, bearing_mapped=False,
                completeness=0.0, ranges_valid=False,
                accuracy=0.0, accuracy_reasons=[],
                consistency_valid=False, consistency_reasons=[],
                missing_fields=[], range_reasons=[asset_reason], cfg=cfg,
            )
            if cfg.log_rejected:
                logger.info("REJECTED [%s] asset mapping — %s",
                            fact.telemetry_id, asset_reason)
            decision = evaluate_routing(ValidationStatus.REJECTED.value, cfg.routes)
            return TrustedBearingSignal(
                raw=fact, validation=validation,
                data_quality_score=0.0,
                validation_status=ValidationStatus.REJECTED,
                quality_report=report,
                processed_at=processed_at,
                normalization_actions=normalized.actions,
                unknown_fields=normalized.unknown_fields,
                record_age_seconds=freshness.age_seconds,
                downstream_eligible=decision.downstream_eligible,
                next_route=decision.route,
                routing_reason=decision.reason,
                **self._provenance(source_profile),
            )

        # ── Step 3: Bearing + channel mapping  (HARD REJECT) ────────────
        bearing_ok, bearing_reason = validate_bearing_id(
            fact.bearing_id, fact.channel_id,
            self._bearings, self._channels
        )
        validation.bearing_mapping = bearing_ok
        if not bearing_ok:
            validation.reasons.append(bearing_reason)
            report = self._build_report(
                asset_mapped=True, bearing_mapped=False,
                completeness=0.0, ranges_valid=False,
                accuracy=0.0, accuracy_reasons=[],
                consistency_valid=False, consistency_reasons=[],
                missing_fields=[], range_reasons=[bearing_reason], cfg=cfg,
            )
            if cfg.log_rejected:
                logger.info("REJECTED [%s] bearing mapping — %s",
                            fact.telemetry_id, bearing_reason)
            decision = evaluate_routing(ValidationStatus.REJECTED.value, cfg.routes)
            return TrustedBearingSignal(
                raw=fact, validation=validation,
                data_quality_score=0.0,
                validation_status=ValidationStatus.REJECTED,
                quality_report=report,
                processed_at=processed_at,
                normalization_actions=normalized.actions,
                unknown_fields=normalized.unknown_fields,
                record_age_seconds=freshness.age_seconds,
                downstream_eligible=decision.downstream_eligible,
                next_route=decision.route,
                routing_reason=decision.reason,
                **self._provenance(source_profile),
            )

        asset_record   = self._assets[fact.asset_id]
        bearing_record = self._bearings[fact.bearing_id]

        # ************** Added by Prateek Mittal on 16th July 2026 ******************
        # Complete the identity chain by verifying that the bearing belongs to
        # the supplied asset, not merely that bearing and channel agree.
        relationship_ok, relationship_reason = validate_asset_bearing_relationship(
            fact.asset_id, bearing_record
        )
        if not relationship_ok:
            validation.bearing_mapping = False
            validation.reasons.append(relationship_reason)
            return self._reject(
                normalized.record, validation, processed_at, relationship_reason,
                normalization_actions=normalized.actions,
                unknown_fields=normalized.unknown_fields,
                record_age_seconds=freshness.age_seconds,
            )
        # ***********************

        # ── Step 4: Range checks  (Validity / Conformity) ───────────────
        ranges_ok, range_reasons = validate_ranges(signal_dict, cfg, bearing_record)
        validation.ranges_valid = ranges_ok
        if not ranges_ok:
            validation.reasons.extend(range_reasons)

        # ── Step 5: Accuracy  (historian signal-quality / sensor fidelity) ─
        accuracy, accuracy_reasons = compute_accuracy_score(signal_dict, cfg)
        validation.accuracy_ok = (accuracy >= cfg.min_signal_quality_score)
        if accuracy_reasons:
            validation.reasons.extend(accuracy_reasons)

        # ── Step 6: Consistency  (cross-field logical agreement) ────────
        consistency_ok, consistency_reasons = check_consistency(signal_dict, cfg)
        validation.consistency_valid = consistency_ok
        if not consistency_ok:
            validation.reasons.extend(consistency_reasons)

        # ── Step 7: Completeness ─────────────────────────────────────────
        completeness, missing_fields = compute_completeness_score(signal_dict, cfg)
        validation.fields_complete = (completeness == 1.0)
        if missing_fields:
            validation.reasons.append(
                f"Missing critical fields: {', '.join(missing_fields)}. "
                f"Fix: ensure historian publishes these fields for every record"
            )

        # ── Step 8: Enrich ───────────────────────────────────────────────
        asset_ctx   = build_asset_context(asset_record)
        bearing_ctx = build_bearing_context(bearing_record)

        # ── Step 9: Quality score ────────────────────────────────────────
        dq_score = compute_data_quality_score(
            completeness   = completeness,
            ranges_valid   = ranges_ok,
            accuracy       = accuracy,
            consistency    = consistency_ok,
            asset_mapped   = True,
            bearing_mapped = True,
            cfg            = cfg,
        )

        # ── Step 10: Status ──────────────────────────────────────────────
        substantive_reasons = [
            r for r in validation.reasons
            if not any(kw in r for kw in cfg.advisory_reason_keywords)
        ]

        if dq_score >= cfg.flagged_threshold and not substantive_reasons and freshness.valid:
            status = ValidationStatus.VALID
        else:
            status = ValidationStatus.FLAGGED

        if fact.startup_shutdown_flag:
            validation.reasons.append(
                "startup_shutdown_flag=true — "
                "Monitoring Agent must suppress anomaly detection"
            )
            # Don't downgrade purely because of startup flag
            non_startup = [
                r for r in validation.reasons
                if "startup_shutdown" not in r
            ]
            if status == ValidationStatus.FLAGGED and not non_startup:
                status = ValidationStatus.VALID

        # ── Step 11: Quality report ──────────────────────────────────────
        report = self._build_report(
            asset_mapped        = True,
            bearing_mapped      = True,
            completeness        = completeness,
            ranges_valid        = ranges_ok,
            accuracy            = accuracy,
            accuracy_reasons    = accuracy_reasons,
            consistency_valid   = consistency_ok,
            consistency_reasons = consistency_reasons,
            missing_fields      = missing_fields,
            range_reasons       = range_reasons,
            cfg                 = cfg,
        )

        if cfg.log_rejected and status == ValidationStatus.REJECTED:
            logger.info("REJECTED [%s] %s/%s score=%.3f",
                        fact.telemetry_id, fact.asset_id, fact.bearing_id, dq_score)
        elif cfg.log_flagged and status == ValidationStatus.FLAGGED:
            logger.info("FLAGGED  [%s] %s/%s score=%.3f reasons=%s",
                        fact.telemetry_id, fact.asset_id, fact.bearing_id,
                        dq_score, substantive_reasons)
        elif cfg.log_valid and status == ValidationStatus.VALID:
            logger.info("VALID    [%s] %s/%s score=%.3f",
                        fact.telemetry_id, fact.asset_id, fact.bearing_id, dq_score)

        # ************** Added by Prateek Mittal on 16th July 2026 ******************
        # Emit an explicit, auditable downstream route with normalization,
        # freshness, and provenance metadata.
        decision = evaluate_routing(status.value, cfg.routes)
        return TrustedBearingSignal(
            raw                = fact,
            asset_ctx          = asset_ctx,
            bearing_ctx        = bearing_ctx,
            validation         = validation,
            data_quality_score = dq_score,
            validation_status  = status,
            quality_report     = report,
            processed_at       = processed_at,
            normalization_actions = normalized.actions,
            unknown_fields        = normalized.unknown_fields,
            record_age_seconds    = freshness.age_seconds,
            downstream_eligible   = decision.downstream_eligible,
            next_route            = decision.route,
            routing_reason        = decision.reason,
            **self._provenance(source_profile),
        )
        # ***********************

    # ************** Added by Prateek Mittal on 16th July 2026 ******************
    # Durable ingestion path: validate, reject duplicates, detect late events,
    # persist the complete decision, and fail safely on repository errors.
    def process_and_store(self, raw: Dict[str, Any]) -> TrustedBearingSignal:
        """Validate one record, enforce ingestion idempotency/order, and persist it."""
        if self._repository is None:
            raise RuntimeError("process_and_store requires a trusted-signal repository")

        result = self.process(raw)
        telemetry_id = result.raw.telemetry_id
        if self._cfg.reject_duplicates and self._repository.exists(telemetry_id):
            result.duplicate_detected = True
            result.persistence_status = "duplicate_not_stored"
            result.downstream_eligible = False
            result.next_route = "stop"
            result.routing_reason = "duplicate telemetry_id already persisted"
            return result

        event_epoch = None
        try:
            text = result.raw.timestamp_utc
            parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
            if parsed.tzinfo is not None:
                event_epoch = parsed.timestamp()
        except (TypeError, ValueError):
            pass

        latest = self._repository.latest_event_epoch(result.raw.bearing_id)
        if (self._cfg.flag_out_of_order and event_epoch is not None and latest is not None
                and event_epoch < latest):
            result.out_of_order = True
            result.validation_status = ValidationStatus.FLAGGED
            result.validation.reasons.append("event timestamp is older than the latest persisted bearing event")
            decision = evaluate_routing(ValidationStatus.FLAGGED.value, self._cfg.routes)
            result.downstream_eligible = decision.downstream_eligible
            result.next_route = decision.route
            result.routing_reason = decision.reason

        try:
            result.persistence_status = "stored"
            self._repository.save(result, event_epoch)
        except Exception as exc:
            result.persistence_status = "failed"
            result.downstream_eligible = False
            result.next_route = "data_review"
            result.routing_reason = f"persistence failed: {exc}"
        return result

    @staticmethod
    def _hash_payload(payload) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def _provenance(self, source_profile: str) -> Dict[str, str]:
        return {
            "schema_version": "1.2",
            "config_version": self._config_version,
            "master_data_version": self._master_data_version,
            "source_profile": source_profile if source_profile in self._cfg.source_profiles else "default",
        }
    # ***********************

    # ------------------------------------------------------------------
    # Quality report builder
    # ------------------------------------------------------------------

    def _build_report(
        self,
        asset_mapped:        bool,
        bearing_mapped:      bool,
        completeness:        float,
        ranges_valid:        bool,
        accuracy:            float,
        accuracy_reasons:    list,
        consistency_valid:   bool,
        consistency_reasons: list,
        missing_fields:      list,
        range_reasons:       list,
        cfg:                 DFAConfig,
    ) -> "QualityReport":
        """
        Builds a QualityReport with one QualityDimension per scoring axis.
        Each dimension carries its score, weight, pass/fail, a plain-language
        description of what it measures, the reason it failed, and a concrete
        improvement hint so data engineers know exactly what to fix.
        """
        w      = cfg.weights
        hints  = cfg.improvement_hints
        labels = cfg.dimension_labels
        descs  = cfg.dimension_descriptions

        dims = [
            QualityDimension(
                name        = "asset_mapping",
                label       = labels["asset_mapping"],
                description = descs["asset_mapping"],
                weight      = w["asset_mapping"],
                score       = 1.0 if asset_mapped else 0.0,
                passed      = asset_mapped,
                reason      = "" if asset_mapped else "asset_id not found in master table",
                hint        = "" if asset_mapped else hints["asset_mapping"],
            ),
            QualityDimension(
                name        = "bearing_mapping",
                label       = labels["bearing_mapping"],
                description = descs["bearing_mapping"],
                weight      = w["bearing_mapping"],
                score       = 1.0 if bearing_mapped else 0.0,
                passed      = bearing_mapped,
                reason      = "" if bearing_mapped else "bearing_id or channel_id not found",
                hint        = "" if bearing_mapped else hints["bearing_mapping"],
            ),
            QualityDimension(
                name        = "completeness",
                label       = labels["completeness"],
                description = descs["completeness"],
                weight      = w["completeness"],
                score       = completeness,
                passed      = completeness == 1.0,
                reason      = (
                    f"Missing fields: {', '.join(missing_fields)}"
                    if missing_fields else ""
                ),
                hint        = hints["completeness"] if missing_fields else "",
            ),
            QualityDimension(
                name        = "ranges_valid",
                label       = labels["ranges_valid"],
                description = descs["ranges_valid"],
                weight      = w["ranges_valid"],
                score       = 1.0 if ranges_valid else 0.0,
                passed      = ranges_valid,
                reason      = "; ".join(range_reasons) if range_reasons else "",
                hint        = hints["ranges_valid"] if range_reasons else "",
            ),
            QualityDimension(
                name        = "accuracy",
                label       = labels["accuracy"],
                description = descs["accuracy"],
                weight      = w["accuracy"],
                score       = accuracy,
                passed      = accuracy >= cfg.min_signal_quality_score,
                reason      = "; ".join(accuracy_reasons) if accuracy_reasons else "",
                hint        = hints["accuracy"] if accuracy_reasons else "",
            ),
            QualityDimension(
                name        = "consistency",
                label       = labels["consistency"],
                description = descs["consistency"],
                weight      = w["consistency"],
                score       = 1.0 if consistency_valid else 0.0,
                passed      = consistency_valid,
                reason      = "; ".join(consistency_reasons) if consistency_reasons else "",
                hint        = hints["consistency"] if consistency_reasons else "",
            ),
        ]

        weighted_score = round(
            sum(d.weight * d.score for d in dims), 4
        )

        return QualityReport(
            overall_score = weighted_score,
            dimensions    = dims,
        )

    # ------------------------------------------------------------------
    # Rejection helper
    # ------------------------------------------------------------------

    def _reject(
        self, raw, validation: ValidationDetail,
        processed_at: str, reason: str,
        normalization_actions=None, unknown_fields=None,
        record_age_seconds=None,
    ) -> TrustedBearingSignal:
        validation.reasons.append(reason)
        fact = BearingSignalFact(
            telemetry_id  = (raw.get("telemetry_id", "UNKNOWN")
                             if isinstance(raw, dict) else "UNKNOWN"),
            timestamp_utc = (raw.get("timestamp_utc", "")
                             if isinstance(raw, dict) else ""),
            asset_id      = (raw.get("asset_id", "UNKNOWN")
                             if isinstance(raw, dict) else "UNKNOWN"),
            bearing_id    = (raw.get("bearing_id", "UNKNOWN")
                             if isinstance(raw, dict) else "UNKNOWN"),
            channel_id    = (raw.get("channel_id", "UNKNOWN")
                             if isinstance(raw, dict) else "UNKNOWN"),
        )
        cfg = self._cfg
        report = QualityReport(
            overall_score = 0.0,
            dimensions    = [
                QualityDimension(
                    name="asset_mapping", label=cfg.dimension_labels["asset_mapping"],
                    description=cfg.dimension_descriptions["asset_mapping"],
                    weight=cfg.weights["asset_mapping"], score=0.0, passed=False,
                    reason=reason, hint=cfg.improvement_hints["asset_mapping"],
                )
            ],
        )
        decision = evaluate_routing(ValidationStatus.REJECTED.value, cfg.routes)
        return TrustedBearingSignal(
            raw=fact, validation=validation,
            data_quality_score=0.0,
            validation_status=ValidationStatus.REJECTED,
            quality_report=report,
            processed_at=processed_at,
            normalization_actions=normalization_actions or [],
            unknown_fields=unknown_fields or [],
            record_age_seconds=record_age_seconds,
            downstream_eligible=decision.downstream_eligible,
            next_route=decision.route,
            routing_reason=decision.reason,
            **self._provenance(str(raw.get("data_source", "default")).lower() if isinstance(raw, dict) else "default"),
        )

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    def process_batch(self, raw_records: list) -> list:
        return [self.process(r) for r in raw_records]

    # ************** Added by Prateek Mittal on 16th July 2026 ******************
    # Batch durable ingestion and repository-aware factories.
    def process_and_store_batch(self, raw_records: list) -> list:
        return [self.process_and_store(record) for record in raw_records]

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def from_data_files(cls, repository=None) -> "DataFoundationAgent":
        """Load lookups and config from standard file locations."""
        from src.tools.data_loader import load_asset_master, load_bearing_master
        asset_lookup             = load_asset_master()
        bearing_lookup, channel_lookup = load_bearing_master()
        cfg                      = load_config()
        return cls(asset_lookup, bearing_lookup, channel_lookup, cfg, repository=repository)

    @classmethod
    def with_sqlite_repository(cls, path: str = None) -> "DataFoundationAgent":
        """Factory for durable local ingestion with duplicate/order checks."""
        from src.tools.trusted_signal_repository import SQLiteTrustedSignalRepository
        cfg = load_config()
        repository = SQLiteTrustedSignalRepository(path or cfg.repository_sqlite_path)
        return cls.from_data_files(repository=repository)
    # ***********************
