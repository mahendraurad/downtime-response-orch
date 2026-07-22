"""Run one of the five diagnostic agents against bundled telemetry."""

import argparse
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agents.data_foundation_agent import DataFoundationAgent
from src.agents.monitoring_agent import MonitoringAgent
from src.agents.failure_intelligence_agent import FailureIntelligenceAgent
from src.agents.predictive_risk_agent import PredictiveRiskAgent
from src.agents.knowledge_agent import KnowledgeAgent
from src.tools.data_loader import load_all_telemetry_rows


def serializable(value):
    if is_dataclass(value):
        return {key: serializable(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return serializable(value.model_dump())
    if hasattr(value, "__dict__"):
        return {key: serializable(item) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("agent", choices=["data", "monitoring", "failure", "risk", "knowledge"])
    parser.add_argument("--row", type=int, default=-1, help="Telemetry row index (default: last row)")
    # ************** Added by Prateek Mittal on 16th July 2026 ******************
    # Optional durable Agent 1 debug mode.
    parser.add_argument("--store", metavar="SQLITE_PATH", help="Persist Agent 1 output and enforce duplicate/order checks")
    # ***********************
    args = parser.parse_args()

    raw = load_all_telemetry_rows()[args.row]
    # ************** Added by Prateek Mittal on 16th July 2026 ******************
    # Select stateless validation or repository-backed ingestion.
    if args.store:
        data_agent = DataFoundationAgent.with_sqlite_repository(args.store)
        trusted = data_agent.process_and_store(raw)
    else:
        trusted = DataFoundationAgent.from_data_files().process(raw)
    # ***********************
    result = trusted

    if args.agent != "data":
        anomaly = MonitoringAgent.from_config().process(trusted)
        result = anomaly
        if anomaly is None:
            print("Monitoring found no anomaly; downstream agents were not run.")
            return

    if args.agent in {"failure", "risk", "knowledge"}:
        diagnosis = FailureIntelligenceAgent.from_data_files().process(anomaly, trusted)
        result = diagnosis

    if args.agent in {"risk", "knowledge"}:
        result = PredictiveRiskAgent.from_data_files().process(diagnosis, anomaly, trusted)

    if args.agent == "knowledge":
        result = KnowledgeAgent().process(diagnosis, trusted)

    print(json.dumps(serializable(result), indent=2, default=str))


if __name__ == "__main__":
    main()