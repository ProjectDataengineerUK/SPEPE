"""Simulador D'Hondt para eleições proporcionais — Deputado Federal.

Uso:
    from mlops.dhondt_simulator import DHondtSimulator

    sim = DHondtSimulator(n_cadeiras=46)
    resultado = sim.simulate(votos_por_candidato, partido_por_candidato)
    print(resultado.eleitos)
    print(resultado.probabilidades)

Para incerteza (Monte Carlo):
    probs = sim.monte_carlo(
        votos_media={"Eduardo Paes": 120_000, "Gracyanne Barbosa": 90_000, ...},
        votos_std={"Eduardo Paes": 20_000, "Gracyanne Barbosa": 15_000, ...},
        partido_por_candidato={...},
        n_simulacoes=10_000,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("spepe.mlops.dhondt_simulator")

# RJ — 46 cadeiras de deputado federal (número fixo; 2022 e 2026)
N_CADEIRAS_RJ = 46

# Quociente mínimo para ter direito a cadeira (Código Eleitoral art. 106)
# Na prática, partido/federação precisa atingir o quociente eleitoral
MIN_QUOCIENTE_VOTOS_FRACTION = 0.0  # simplificado; TSE usa quociente eleitoral inteiro


@dataclass
class DHondtResult:
    votos_por_candidato: dict[str, int]
    votos_por_partido: dict[str, int]
    quociente_eleitoral: float
    cadeiras_por_partido: dict[str, int]
    eleitos: list[str]
    ranking_por_partido: dict[str, list[str]]
    total_votos_validos: int

    def to_df(self) -> pd.DataFrame:
        rows = []
        for partido, candidatos in self.ranking_por_partido.items():
            cadeiras = self.cadeiras_por_partido.get(partido, 0)
            for i, cand in enumerate(candidatos):
                rows.append(
                    {
                        "candidato": cand,
                        "partido": partido,
                        "votos": self.votos_por_candidato.get(cand, 0),
                        "posicao_nominata": i + 1,
                        "cadeiras_partido": cadeiras,
                        "eleito": cand in self.eleitos,
                    }
                )
        return pd.DataFrame(rows).sort_values("votos", ascending=False)


@dataclass
class MonteCarloResult:
    n_simulacoes: int
    probabilidade_eleicao: dict[str, float]
    votos_medio: dict[str, float]
    votos_p10: dict[str, float]
    votos_p90: dict[str, float]
    cadeiras_medio_por_partido: dict[str, float]

    def to_df(self) -> pd.DataFrame:
        rows = [
            {
                "candidato": nm,
                "prob_eleicao": self.probabilidade_eleicao[nm],
                "votos_medio": self.votos_medio.get(nm, 0),
                "votos_p10": self.votos_p10.get(nm, 0),
                "votos_p90": self.votos_p90.get(nm, 0),
            }
            for nm in self.probabilidade_eleicao
        ]
        df = pd.DataFrame(rows)
        return df.sort_values("prob_eleicao", ascending=False)


class DHondtSimulator:
    """Simulador de eleições proporcionais usando método D'Hondt (Código Eleitoral BR)."""

    def __init__(self, n_cadeiras: int = N_CADEIRAS_RJ) -> None:
        self.n_cadeiras = n_cadeiras

    # ── Core D'Hondt ──────────────────────────────────────────────────────────

    def _aggregate_party_votes(
        self,
        votos_por_candidato: dict[str, int],
        partido_por_candidato: dict[str, str],
    ) -> dict[str, int]:
        totals: dict[str, int] = {}
        for cand, votos in votos_por_candidato.items():
            partido = partido_por_candidato.get(cand, "OUTROS")
            totals[partido] = totals.get(partido, 0) + votos
        return totals

    def _dhondt_seats(self, votos_por_partido: dict[str, int]) -> dict[str, int]:
        """Distribute seats using the D'Hondt method."""
        partidos = list(votos_por_partido.keys())
        votos = np.array([votos_por_partido[p] for p in partidos], dtype=float)

        # quociente eleitoral (Código Eleitoral BR art. 106)
        total = votos.sum()
        if total == 0:
            return {p: 0 for p in partidos}
        quociente = total / self.n_cadeiras

        # Only parties above quociente get seats (art. 106 §2º simplified)
        eligible = votos >= quociente

        # D'Hondt divisors: 1, 2, 3, ..., n_cadeiras
        cadeiras: dict[str, int] = {p: 0 for p in partidos}
        for _ in range(self.n_cadeiras):
            # Quotients: votos[i] / (cadeiras[i] + 1) for eligible parties
            quotients = np.where(
                eligible,
                votos / (np.array([cadeiras[p] for p in partidos]) + 1),
                -1.0,
            )
            winner_idx = int(np.argmax(quotients))
            if quotients[winner_idx] < 0:
                break
            cadeiras[partidos[winner_idx]] += 1

        return cadeiras

    def simulate(
        self,
        votos_por_candidato: dict[str, int],
        partido_por_candidato: dict[str, str],
    ) -> DHondtResult:
        """Run a single D'Hondt simulation."""
        votos_por_partido = self._aggregate_party_votes(votos_por_candidato, partido_por_candidato)
        total_votos = sum(votos_por_candidato.values())
        quociente = total_votos / self.n_cadeiras if total_votos > 0 else 0.0

        cadeiras = self._dhondt_seats(votos_por_partido)

        # Rank candidates within each party by descending votes
        ranking: dict[str, list[str]] = {}
        for cand, partido in partido_por_candidato.items():
            ranking.setdefault(partido, [])
            ranking[partido].append(cand)
        for partido in ranking:
            ranking[partido].sort(key=lambda c: votos_por_candidato.get(c, 0), reverse=True)

        eleitos: list[str] = []
        for partido, cands in ranking.items():
            n = cadeiras.get(partido, 0)
            eleitos.extend(cands[:n])

        return DHondtResult(
            votos_por_candidato=votos_por_candidato,
            votos_por_partido=votos_por_partido,
            quociente_eleitoral=quociente,
            cadeiras_por_partido=cadeiras,
            eleitos=eleitos,
            ranking_por_partido=ranking,
            total_votos_validos=total_votos,
        )

    # ── Monte Carlo ────────────────────────────────────────────────────────────

    def monte_carlo(
        self,
        votos_media: dict[str, float],
        partido_por_candidato: dict[str, str],
        votos_std: Optional[dict[str, float]] = None,
        n_simulacoes: int = 10_000,
        seed: int = 42,
    ) -> MonteCarloResult:
        """Run N Monte Carlo simulations with Gaussian vote noise.

        Args:
            votos_media: expected votes per candidate
            partido_por_candidato: candidate → party mapping
            votos_std: std dev per candidate; defaults to 20% of votos_media
            n_simulacoes: number of simulations
            seed: random seed for reproducibility
        """
        rng = np.random.default_rng(seed)
        candidatos = list(votos_media.keys())

        if votos_std is None:
            votos_std = {c: max(v * 0.20, 500) for c, v in votos_media.items()}

        mu = np.array([votos_media[c] for c in candidatos])
        sigma = np.array([votos_std.get(c, mu[i] * 0.20) for i, c in enumerate(candidatos)])

        eleicoes: dict[str, int] = {c: 0 for c in candidatos}
        all_votos: list[np.ndarray] = []

        for _ in range(n_simulacoes):
            sample = np.maximum(0, rng.normal(mu, sigma)).astype(int)
            votos_sim = dict(zip(candidatos, sample))
            result = self.simulate(votos_sim, partido_por_candidato)
            for eleito in result.eleitos:
                eleicoes[eleito] = eleicoes.get(eleito, 0) + 1
            all_votos.append(sample)

        votos_arr = np.array(all_votos)
        prob = {c: eleicoes[c] / n_simulacoes for c in candidatos}

        # Cadeiras médias por partido
        cadeiras_medio: dict[str, float] = {}
        for _ in range(min(1000, n_simulacoes)):
            idx = rng.integers(0, n_simulacoes)
            votos_sim = dict(zip(candidatos, all_votos[idx]))
            r = self.simulate(votos_sim, partido_por_candidato)
            for p, c in r.cadeiras_por_partido.items():
                cadeiras_medio[p] = cadeiras_medio.get(p, 0) + c
        for p in cadeiras_medio:
            cadeiras_medio[p] /= min(1000, n_simulacoes)

        return MonteCarloResult(
            n_simulacoes=n_simulacoes,
            probabilidade_eleicao=prob,
            votos_medio={c: float(votos_arr[:, i].mean()) for i, c in enumerate(candidatos)},
            votos_p10={
                c: float(np.percentile(votos_arr[:, i], 10)) for i, c in enumerate(candidatos)
            },
            votos_p90={
                c: float(np.percentile(votos_arr[:, i], 90)) for i, c in enumerate(candidatos)
            },
            cadeiras_medio_por_partido=cadeiras_medio,
        )


# ── Feature builder ────────────────────────────────────────────────────────────


def build_nominata_features(
    votos_historicos: dict[str, int],
    partido_por_candidato: dict[str, str],
    n_cadeiras: int = N_CADEIRAS_RJ,
) -> pd.DataFrame:
    """Build nominata-level features for each candidate.

    Returns DataFrame with:
      - forca_nominata: party's total historical votes / quociente_estimado
      - posicao_estimada_nominata: rank within party by historical votes
      - cadeiras_estimadas_partido: D'Hondt estimate from historical data
      - prob_eleicao_historica: fraction of times elected if history repeated exactly
    """
    sim = DHondtSimulator(n_cadeiras=n_cadeiras)
    result = sim.simulate(votos_historicos, partido_por_candidato)

    rows = []
    for partido, cands in result.ranking_por_partido.items():
        cadeiras = result.cadeiras_por_partido.get(partido, 0)
        votos_partido = result.votos_por_partido.get(partido, 0)
        forca = votos_partido / (result.quociente_eleitoral + 1e-6)
        for i, cand in enumerate(cands):
            rows.append(
                {
                    "candidato": cand,
                    "partido": partido,
                    "votos_historico": votos_historicos.get(cand, 0),
                    "posicao_estimada_nominata": i + 1,
                    "cadeiras_estimadas_partido": cadeiras,
                    "forca_nominata": round(forca, 3),
                    "eleito_historico": cand in result.eleitos,
                    "quociente_eleitoral_historico": round(result.quociente_eleitoral),
                }
            )

    df = pd.DataFrame(rows)
    # Concentration index: fraction of votes in top-5 municipalities not available here
    # (needs municipal-level data — computed in electoral_dataset_builder)
    return df
