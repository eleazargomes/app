"""Motor de regras: KM atual, idade, status, urgência e KPIs."""
from datetime import date

import config
from normalizer import meta_vigente, normalizar_chassi

STATUS = {
    "REGULAR":           {"label": "Regular",            "cor": "#10B981"},
    "ALERTA":            {"label": "Alerta preventivo",  "cor": "#F59E0B"},
    "CRITICO":           {"label": "Crítico / Vencido",  "cor": "#EF4444"},
    "DESATIVADO_TEMPO":  {"label": "Desativado por tempo", "cor": "#6B7280"},
    "DESATIVADO_KM":     {"label": "Desativado por KM",  "cor": "#6B7280"},
    "SEM_CRITERIO":      {"label": "Sem critério",       "cor": "#94A3B8"},
}


def km_atual_por_veiculo(abastecimentos):
    """
    Regra C: KM atual = registro com o MAIOR km_registrada.
    Também sinaliza leitura suspeita (maior valor >2x o segundo maior distinto),
    pois um erro de digitação no abastecimento desativaria o veículo à toa.
    """
    valores = {}
    for a in abastecimentos:
        km = a.get("km_registrada")
        if km is None:
            continue
        valores.setdefault(a["prefixo"], set()).add(int(km))
    out = {}
    for prefixo, vs in valores.items():
        ordenado = sorted(vs, reverse=True)
        maximo = ordenado[0]
        segundo = ordenado[1] if len(ordenado) > 1 else None
        suspeito = bool(segundo and segundo > 0 and maximo > 2 * segundo)
        out[prefixo] = {"km": maximo, "suspeito": suspeito,
                        "km_alternativo": segundo if suspeito else None}
    return out


def idade_em_meses(ano_chassi, hoje=None):
    hoje = hoje or date.today()
    return (hoje.year - int(ano_chassi)) * 12 + (hoje.month - 1)


def achar_criterio(criterios, tipo_chassi, filial):
    """Critério específico da filial tem prioridade sobre o global ('*')."""
    especifico = globais = None
    for c in criterios:
        if c["tipo_chassi"] != tipo_chassi:
            continue
        if c["filial"] == filial:
            especifico = c
        elif c["filial"] in ("*", ""):
            globais = c
    return especifico or globais


def avaliar_veiculo(v, criterio, km_info, ultima_revisao, hoje=None):
    hoje = hoje or date.today()
    res = {
        "prefixo": v["prefixo"], "filial": v["filial"],
        "tipo_chassi": v["tipo_chassi"], "ano_chassi": v["ano_chassi"],
        "km_atual": km_info["km"] if km_info else None,
        "km_suspeito": bool(km_info and km_info["suspeito"]),
        "km_alternativo": km_info["km_alternativo"] if km_info else None,
        "sem_leitura_km": km_info is None,
        "idade_meses": None, "tempo_max_meses": None, "meses_restantes": None,
        "plano_km": None, "meta_km": None, "meta_origem": None,
        "km_restante": None, "urgencia": 0.0, "status": "SEM_CRITERIO",
        "ultima_revisao": ultima_revisao["data"] if ultima_revisao else None,
    }
    if v.get("ano_chassi"):
        res["idade_meses"] = idade_em_meses(v["ano_chassi"], hoje)
    if not criterio:
        res.update(STATUS_INFO(res["status"]))
        return res

    plano, tmax = criterio["plano_km"], criterio["tempo_max_meses"]
    res["plano_km"], res["tempo_max_meses"] = plano, tmax
    status, urg = "REGULAR", 0.0

    # --- tempo (regra D) ---
    if tmax and res["idade_meses"] is not None:
        res["meses_restantes"] = tmax - res["idade_meses"]
        urg = max(urg, res["idade_meses"] / tmax)
        if res["idade_meses"] > tmax:
            status = "DESATIVADO_TEMPO"
        elif res["meses_restantes"] <= config.ALERTA_TEMPO_MESES:
            status = "ALERTA"

    # --- KM (regras C/D/E) ---
    if plano and res["km_atual"] is not None:
        meta, origem = meta_vigente(plano, res["km_atual"], ultima_revisao)
        res["meta_km"], res["meta_origem"] = meta, origem
        res["km_restante"] = meta - res["km_atual"]
        urg = max(urg, (res["km_atual"] - (meta - plano)) / plano)
        if status != "DESATIVADO_TEMPO":
            excesso = res["km_atual"] - meta
            if config.KM_DESATIVAR_APOS_EXCESSO and excesso > config.KM_DESATIVAR_APOS_EXCESSO:
                status = "DESATIVADO_KM"
            elif res["km_atual"] >= meta:
                status = "CRITICO"
            elif res["km_restante"] <= plano * config.ALERTA_KM_PCT and status == "REGULAR":
                status = "ALERTA"
    res["status"], res["urgencia"] = status, round(urg, 4)
    res.update(STATUS_INFO(status))
    return res


def STATUS_INFO(status):
    return {"status_label": STATUS[status]["label"], "status_cor": STATUS[status]["cor"]}


def avaliar_frota(veiculos, criterios, abastecimentos, revisoes, hoje=None):
    kms = km_atual_por_veiculo(abastecimentos)
    ultimas = {}
    for r in revisoes:                         # mais recente = maior meta concluída
        atual = ultimas.get(r["prefixo"])
        if not atual or (r.get("proxima_meta_km") or 0) >= (atual.get("proxima_meta_km") or 0):
            ultimas[r["prefixo"]] = r
    out = []
    for v in veiculos:
        crit = achar_criterio(criterios, v["tipo_chassi"], v["filial"])
        out.append(avaliar_veiculo(v, crit, kms.get(v["prefixo"]),
                                   ultimas.get(v["prefixo"]), hoje))
    return out


def kpis(avaliados):
    total = len(avaliados)
    cont = lambda *s: sum(1 for a in avaliados if a["status"] in s)
    return {
        "total": total,
        "regulares": cont("REGULAR"),
        "alerta": cont("ALERTA"),
        "criticos": cont("CRITICO"),
        "desativados": cont("DESATIVADO_TEMPO", "DESATIVADO_KM"),
        "sem_criterio": cont("SEM_CRITERIO"),
        "sem_leitura_km": sum(1 for a in avaliados if a["sem_leitura_km"]),
        "km_suspeito": sum(1 for a in avaliados if a["km_suspeito"]),
    }
