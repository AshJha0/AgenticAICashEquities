"""Specialist agents.

Each agent answers one question:

* Planner     - what should we investigate, in which order?
* Market      - what happened in the market?
* Execution   - what happened to our orders?
* Quant       - what does the data statistically show, and what caused it?
* Risk        - did the behaviour create unusual exposure or limit usage?
* Engineering - did our technology behave normally?
* Critic      - are these conclusions actually supported?
* Reporter    - write the auditable report (numbers come from the evidence).
"""

from ceap.agents.critic import CriticAgent
from ceap.agents.engineering import EngineeringAgent
from ceap.agents.execution import ExecutionAgent
from ceap.agents.market import MarketAgent
from ceap.agents.planner import PlannerAgent
from ceap.agents.quant import QuantAgent
from ceap.agents.reporter import ReportAgent
from ceap.agents.risk import RiskAgent

__all__ = [
    "CriticAgent",
    "EngineeringAgent",
    "ExecutionAgent",
    "MarketAgent",
    "PlannerAgent",
    "QuantAgent",
    "ReportAgent",
    "RiskAgent",
]
