"""
Normalização de chassi e cálculo do ciclo de revisões.

Estratégia de chassi (importante):
  1. A normalização por regex/regras é DETERMINÍSTICA e é a única que agrupa
     automaticamente ("LO916", "LO 916", "LO-916 EURO VI" -> "LO-916").
  2. A distância de Levenshtein NÃO funde nada sozinha, porque modelos
     distintos ficam a 1-2 edições um do outro (OF-1721/59 x OF-1724L/59).
     Ela é usada para SUGERIR ("você quis dizer...?") ao digitar um chassi novo.
"""
import re
import unicodedata

_EURO = re.compile(r"\bEURO\s*(?:VI|V|IV|6|5)\b")
_CODIGO = re.compile(r"^([A-Z]{1,3})[\s\-_.]*(\d.*)$")
# "500 R 1931" -> "500R 1931" | "500 RSDD 2745" -> "500RSDD 2745"
_LETRAS_SOLTAS = re.compile(r"(\d)\s+([A-Z]{1,4})(?=\s+\d)")


def _sem_acento(texto):
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalizar_chassi(valor):
    """Retorna a forma canônica de um tipo de chassi ('' se vazio)."""
    if valor is None:
        return ""
    t = _sem_acento(str(valor)).upper().strip()
    if not t:
        return ""
    t = _EURO.sub(" ", t)
    t = re.sub(r"\s*/\s*", "/", t)          # "TOYOTA / ETIOS" -> "TOYOTA/ETIOS"
    t = re.sub(r"\s+", " ", t).strip(" -_.")
    m = _CODIGO.match(t)
    if m:                                    # "OF1621/59" -> "OF-1621/59"
        prefixo, resto = m.groups()
        resto = _LETRAS_SOLTAS.sub(r"\1\2", resto.strip())
        t = f"{prefixo}-{resto}"
    return re.sub(r"\s+", " ", t).strip()


def levenshtein(a, b):
    """Distância de edição (programação dinâmica, O(len(a)*len(b)))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        atual = [i]
        for j, cb in enumerate(b, 1):
            atual.append(min(anterior[j] + 1, atual[j - 1] + 1,
                             anterior[j - 1] + (ca != cb)))
        anterior = atual
    return anterior[-1]


def sugerir_similares(valor, conhecidos, max_dist=2, limite=3):
    """Chassis já cadastrados parecidos com `valor` (para confirmar com o usuário)."""
    alvo = normalizar_chassi(valor)
    if not alvo:
        return []
    achados = []
    for c in set(conhecidos):
        if c == alvo:
            continue
        d = levenshtein(alvo, c)
        if d <= max_dist:
            achados.append((d, c))
    return [c for _, c in sorted(achados)[:limite]]


def agrupar_chassis(valores):
    """{normalizado: [variações originais]} — útil para auditoria no seed."""
    grupos = {}
    for v in valores:
        grupos.setdefault(normalizar_chassi(v), set()).add(str(v).strip())
    return {k: sorted(v) for k, v in grupos.items()}


# ---------------------------------------------------------------------------
# Ciclo de revisões
# ---------------------------------------------------------------------------
def meta_inicial_assumida(plano_km, km_atual):
    """
    Sem histórico de revisão: assume que o veículo está em dia e a próxima meta
    é o próximo múltiplo do plano acima do KM atual. (Marcado como 'ASSUMIDA'.)
    """
    plano_km = int(plano_km)
    if plano_km <= 0:
        raise ValueError("plano_km deve ser > 0")
    return (int(km_atual or 0) // plano_km + 1) * plano_km


def proxima_meta(plano_km, meta_concluida):
    """Revisão de 30.000 concluída -> próxima meta 60.000 (meta + plano)."""
    plano_km = int(plano_km)
    if plano_km <= 0:
        raise ValueError("plano_km deve ser > 0")
    return int(meta_concluida) + plano_km


def meta_vigente(plano_km, km_atual, ultima_revisao):
    """
    Retorna (meta_km, origem). `ultima_revisao` é o registro mais recente de
    Revisoes (dict) ou None. A meta nunca "pula" ciclos atrasados: se o veículo
    está 2 ciclos atrasado, são necessárias 2 baixas.
    """
    if ultima_revisao and ultima_revisao.get("proxima_meta_km"):
        return int(ultima_revisao["proxima_meta_km"]), "REVISAO"
    return meta_inicial_assumida(plano_km, km_atual), "ASSUMIDA"


def normalizar_prefixo(valor):
    """'2070' -> '02070' (padrão do relatório de abastecimento); texto fica em maiúsculas."""
    t = re.sub(r"\s+", " ", str(valor or "")).strip().upper()
    return t.zfill(5) if t.isdigit() else t
