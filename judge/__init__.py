"""Independent ML audit agent.

Isolation requirement: judge/ MUST NOT import from mlops/ or agents/.
The judge is independent — it runs its own backtesting, reads predictions from
BigQuery (spepe_mlops.fact_predictions, shadow=true) and produces a formal
technical parecer (Aprovado / Aprovado com ressalvas / Reprovado).
"""

from judge.ml_judge import MLJudge, JudgeVerdict

__all__ = ["MLJudge", "JudgeVerdict"]
