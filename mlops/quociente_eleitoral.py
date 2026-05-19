"""Simulador de quociente eleitoral para eleições proporcionais (D'Hondt modificado).

Implementa o sistema brasileiro de distribuição de cadeiras:
  1. Quociente Eleitoral (QE) — limiar de entrada partidária
  2. Quociente Partidário (QP) — cadeiras base de cada partido
  3. Maior Média — distribuição das sobras

Cargos suportados: Deputado Federal (cd_cargo=6), Deputado Estadual (cd_cargo=7),
Vereador (cd_cargo=13).

Referência: Lei nº 9.504/1997, arts. 106-109.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Distribuição de vagas de Deputado Federal por UF (Câmara dos Deputados — 513 vagas)
VAGAS_DEP_FEDERAL: dict[str, int] = {
    "AC": 8,
    "AL": 9,
    "AM": 8,
    "AP": 8,
    "BA": 39,
    "CE": 22,
    "DF": 8,
    "ES": 10,
    "GO": 17,
    "MA": 18,
    "MG": 53,
    "MS": 8,
    "MT": 8,
    "PA": 17,
    "PB": 12,
    "PE": 25,
    "PI": 10,
    "PR": 30,
    "RJ": 46,
    "RN": 8,
    "RO": 8,
    "RR": 8,
    "RS": 31,
    "SC": 16,
    "SE": 8,
    "SP": 70,
    "TO": 8,
}


@dataclass
class ResultadoSimulacao:
    sg_uf: str
    cd_cargo: int  # 6=Dep Federal, 7=Dep Estadual, 13=Vereador
    ano_eleicao: int
    vagas: int
    quociente_eleitoral: float
    cadeiras_por_partido: dict[str, int]
    candidatos: list[dict]
    n_simulacoes: int
    noise_pct: float
    # Metadados derivados populados por simulate_election()
    total_votos_validos: int = field(default=0)
    partidos_elegiveis: list[str] = field(default_factory=list)
    cadeiras_distribuidas_qp: int = field(default=0)
    cadeiras_distribuidas_sobras: int = field(default=0)


def simulate_quociente(votos_partido: dict[str, int], vagas: int) -> dict[str, int]:
    """Distribuir cadeiras entre partidos pelo sistema D'Hondt modificado (Brasil).

    Args:
        votos_partido: Mapeamento sg_partido → total de votos nominais (sem brancos/nulos).
        vagas: Total de cadeiras a distribuir na circunscrição.

    Returns:
        Mapeamento sg_partido → número de cadeiras. Partidos com QP < 1 recebem 0.

    Raises:
        ValueError: Se vagas < 1 ou votos_partido estiver vazio.
    """
    if vagas < 1:
        raise ValueError(f"vagas deve ser >= 1, recebido: {vagas}")
    if not votos_partido:
        raise ValueError("votos_partido não pode ser vazio")

    total_votos = sum(v for v in votos_partido.values() if v > 0)
    if total_votos == 0:
        return {p: 0 for p in votos_partido}

    qe = total_votos / vagas

    # Cadeiras base: floor(QP) apenas para partidos com QP >= 1
    cadeiras_base: dict[str, int] = {}
    for partido, votos in votos_partido.items():
        qp = votos / qe
        cadeiras_base[partido] = math.floor(qp) if qp >= 1.0 else 0

    cadeiras_usadas = sum(cadeiras_base.values())
    sobras = vagas - cadeiras_usadas

    # Maior média: votos / (cadeiras_base + 1) — apenas partidos elegíveis (QP >= 1)
    # Partidos sem nenhuma cadeira base mas com votos também concorrem às sobras
    # (na prática brasileira, só elegíveis — mas legislação varia; mantemos elegíveis)
    elegíveis = {p for p, v in votos_partido.items() if v / qe >= 1.0}

    cadeiras_finais = dict(cadeiras_base)
    for _ in range(sobras):
        if not elegíveis:
            break
        media = {p: votos_partido[p] / (cadeiras_finais[p] + 1) for p in elegíveis}
        vencedor = max(media, key=lambda p: (media[p], votos_partido[p]))
        cadeiras_finais[vencedor] += 1

    return cadeiras_finais


def simulate_candidates(
    votos_candidato: list[dict],
    cadeiras_partido: dict[str, int],
) -> list[dict]:
    """Ordenar candidatos por partido e marcar eleitos com base nas cadeiras distribuídas.

    Args:
        votos_candidato: Lista de dicts com chaves:
            nm_candidato, sg_partido, sq_candidato, total_votos
        cadeiras_partido: Saída de simulate_quociente() — sg_partido → n_cadeiras.

    Returns:
        Lista original enriquecida com:
            cadeiras_partido  — cadeiras do partido do candidato
            rank_partido       — posição dentro do partido (1 = mais votado)
            prob_eleicao       — probabilidade estimada de eleição [0, 1]
            eleito_simulado    — bool — eleito na simulação determinística
    """
    # Agrupar por partido e ordenar por votos DESC
    por_partido: dict[str, list[dict]] = {}
    for c in votos_candidato:
        sg = c.get("sg_partido", "")
        por_partido.setdefault(sg, []).append(c)

    resultado: list[dict] = []
    for sg, candidatos in por_partido.items():
        n_cadeiras = cadeiras_partido.get(sg, 0)
        ordenados = sorted(candidatos, key=lambda x: x.get("total_votos", 0), reverse=True)

        for rank_0, cand in enumerate(ordenados):
            rank = rank_0 + 1  # 1-based
            eleito = rank <= n_cadeiras

            if rank <= n_cadeiras:
                prob = 1.0
            elif rank == n_cadeiras + 1:
                # Zona de risco — sobra pode alterar resultado
                prob = 0.3
            else:
                prob = max(0.0, 1.0 - (rank - n_cadeiras) * 0.15)

            resultado.append(
                {
                    **cand,
                    "cadeiras_partido": n_cadeiras,
                    "rank_partido": rank,
                    "prob_eleicao": prob,
                    "eleito_simulado": eleito,
                }
            )

    return resultado


def run_monte_carlo(
    votos_candidato: list[dict],
    vagas: int,
    n_sim: int = 5000,
    noise_pct: float = 0.05,
) -> list[dict]:
    """Monte Carlo para probabilidade de eleição com incerteza eleitoral.

    Adiciona ruído gaussiano nos votos de cada candidato em cada simulação
    para capturar incerteza na apuração / projeção.

    Args:
        votos_candidato: Lista de dicts — mesma estrutura de simulate_candidates().
        vagas: Total de cadeiras na circunscrição.
        n_sim: Número de simulações (default 5000).
        noise_pct: Desvio padrão do ruído como fração dos votos base (default 0.05 = 5%).

    Returns:
        Lista de dicts com: sq_candidato, nm_candidato, sg_partido, votos_base,
        prob_eleicao_mc, ic_lower_95, ic_upper_95, eleito_mc_50pct.
    """
    candidatos = list(votos_candidato)
    n_cand = len(candidatos)

    votos_base = np.array([c.get("total_votos", 0) for c in candidatos], dtype=float)
    partidos = [c.get("sg_partido", "") for c in candidatos]

    # Contagem de vezes eleito por candidato ao longo das simulações
    eleito_count = np.zeros(n_cand, dtype=int)
    # Para IC por simulação: armazenamos flags de eleição (bool array n_sim × n_cand)
    eleito_matrix = np.zeros((n_sim, n_cand), dtype=bool)

    rng = np.random.default_rng(seed=42)

    for s in range(n_sim):
        # Ruído multiplicativo independente por candidato
        noise = rng.normal(loc=1.0, scale=noise_pct, size=n_cand)
        votos_sim = np.maximum(0, votos_base * noise).astype(int)

        # Agregar votos por partido com os votos ruidosos
        votos_partido: dict[str, int] = {}
        for i, p in enumerate(partidos):
            votos_partido[p] = votos_partido.get(p, 0) + int(votos_sim[i])

        cadeiras = simulate_quociente(votos_partido, vagas)

        # Reconstruir lista de candidatos com votos simulados para ordenação intra-partido
        cands_sim = [{**candidatos[i], "total_votos": int(votos_sim[i])} for i in range(n_cand)]
        resultado = simulate_candidates(cands_sim, cadeiras)

        # Mapear resultado de volta para índices originais por sq_candidato
        eleito_map: dict = {
            r.get("sq_candidato"): r.get("eleito_simulado", False) for r in resultado
        }
        for i, cand in enumerate(candidatos):
            if eleito_map.get(cand.get("sq_candidato"), False):
                eleito_count[i] += 1
                eleito_matrix[s, i] = True

    prob_mc = eleito_count / n_sim

    # IC 95% via bootstrap percentis: amostrar blocos de simulações
    block_size = max(1, n_sim // 100)
    n_blocks = n_sim // block_size
    block_probs = np.array(
        [eleito_matrix[b * block_size : (b + 1) * block_size].mean(axis=0) for b in range(n_blocks)]
    )  # shape (n_blocks, n_cand)

    ic_lower = np.percentile(block_probs, 2.5, axis=0)
    ic_upper = np.percentile(block_probs, 97.5, axis=0)

    saida: list[dict] = []
    for i, cand in enumerate(candidatos):
        saida.append(
            {
                "sq_candidato": cand.get("sq_candidato"),
                "nm_candidato": cand.get("nm_candidato"),
                "sg_partido": cand.get("sg_partido"),
                "votos_base": int(votos_base[i]),
                "prob_eleicao_mc": float(prob_mc[i]),
                "ic_lower_95": float(max(0.0, ic_lower[i])),
                "ic_upper_95": float(min(1.0, ic_upper[i])),
                "eleito_mc_50pct": bool(prob_mc[i] >= 0.5),
            }
        )

    return sorted(saida, key=lambda x: x["prob_eleicao_mc"], reverse=True)


def simulate_election(
    df_votos: pd.DataFrame,
    vagas: int,
    n_sim: int = 5000,
) -> ResultadoSimulacao:
    """Simular eleição proporcional a partir de DataFrame de votos.

    Args:
        df_votos: DataFrame com colunas obrigatórias:
            nm_candidato, sg_partido, sq_candidato, total_votos,
            sg_uf, cd_cargo, ano_eleicao
        vagas: Total de cadeiras da circunscrição.
        n_sim: Número de simulações Monte Carlo (default 5000).

    Returns:
        ResultadoSimulacao com QE, cadeiras por partido e lista de candidatos
        enriquecida com probabilidades de eleição.

    Raises:
        ValueError: Se colunas obrigatórias estiverem ausentes.
    """
    _required = {
        "nm_candidato",
        "sg_partido",
        "sq_candidato",
        "total_votos",
        "sg_uf",
        "cd_cargo",
        "ano_eleicao",
    }
    missing = _required - set(df_votos.columns)
    if missing:
        raise ValueError(f"Colunas ausentes no DataFrame: {missing}")

    sg_uf = str(df_votos["sg_uf"].iloc[0])
    cd_cargo = int(df_votos["cd_cargo"].iloc[0])
    ano_eleicao = int(df_votos["ano_eleicao"].iloc[0])

    votos_candidato = df_votos[
        ["nm_candidato", "sg_partido", "sq_candidato", "total_votos"]
    ].to_dict(orient="records")

    # Votos válidos por partido (nominais apenas — sem brancos/nulos)
    votos_partido: dict[str, int] = df_votos.groupby("sg_partido")["total_votos"].sum().to_dict()
    total_votos_validos = int(sum(votos_partido.values()))

    qe = total_votos_validos / vagas if vagas > 0 else 0.0
    cadeiras_por_partido = simulate_quociente(votos_partido, vagas)

    # Candidatos enriquecidos com resultado determinístico
    candidatos_base = simulate_candidates(votos_candidato, cadeiras_por_partido)

    # Monte Carlo sobrepõe prob_eleicao_mc ao resultado determinístico
    mc_results = run_monte_carlo(votos_candidato, vagas, n_sim=n_sim)
    mc_map = {r["sq_candidato"]: r for r in mc_results}

    candidatos_final: list[dict] = []
    for cand in candidatos_base:
        sq = cand.get("sq_candidato")
        mc = mc_map.get(sq, {})
        candidatos_final.append(
            {
                **cand,
                "prob_eleicao_mc": mc.get("prob_eleicao_mc", cand["prob_eleicao"]),
                "ic_lower_95": mc.get("ic_lower_95", cand["prob_eleicao"]),
                "ic_upper_95": mc.get("ic_upper_95", cand["prob_eleicao"]),
                "eleito_mc_50pct": mc.get("eleito_mc_50pct", cand["eleito_simulado"]),
            }
        )

    candidatos_final.sort(key=lambda x: x["prob_eleicao_mc"], reverse=True)

    elegíveis = [p for p, v in votos_partido.items() if v / qe >= 1.0] if qe > 0 else []
    cadeiras_qp = sum(math.floor(votos_partido.get(p, 0) / qe) for p in elegíveis) if qe > 0 else 0
    cadeiras_sobras = sum(cadeiras_por_partido.values()) - cadeiras_qp

    return ResultadoSimulacao(
        sg_uf=sg_uf,
        cd_cargo=cd_cargo,
        ano_eleicao=ano_eleicao,
        vagas=vagas,
        quociente_eleitoral=qe,
        cadeiras_por_partido=cadeiras_por_partido,
        candidatos=candidatos_final,
        n_simulacoes=n_sim,
        noise_pct=0.05,
        total_votos_validos=total_votos_validos,
        partidos_elegiveis=elegíveis,
        cadeiras_distribuidas_qp=cadeiras_qp,
        cadeiras_distribuidas_sobras=cadeiras_sobras,
    )
