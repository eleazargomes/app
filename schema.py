"""Esquema das tabelas (abas da planilha). Espelhado em gas_integration.js."""

SCHEMA = {
    "Usuarios": {
        "key": ["email"],
        "cols": ["email", "nome", "senha_hash", "perfil", "filial"],
        "types": {},
    },
    "Veiculos": {
        "key": ["prefixo"],
        "cols": ["prefixo", "tipo_chassi_original", "tipo_chassi",
                 "ano_chassi", "filial", "criado_em"],
        "types": {"ano_chassi": int},
    },
    "Criterios": {
        "key": ["tipo_chassi", "filial"],
        "cols": ["tipo_chassi", "filial", "plano_km", "tempo_max_meses",
                 "revisar", "atualizado_em"],
        "types": {"plano_km": int, "tempo_max_meses": int},
    },
    "Abastecimentos": {
        "key": [],
        "cols": ["data", "prefixo", "empresa", "km_registrada", "km_rodado",
                 "litros", "posto"],
        "types": {"km_registrada": int, "km_rodado": int, "litros": float},
    },
    "Revisoes": {
        "key": ["id"],
        "cols": ["id", "prefixo", "data", "meta_km_concluida", "proxima_meta_km",
                 "km_no_momento", "responsavel", "nf", "observacoes",
                 "origem", "registrado_por", "criado_em"],
        "types": {"meta_km_concluida": int, "proxima_meta_km": int,
                  "km_no_momento": int},
    },
}


def coerce_row(table, row):
    """Converte tipos (planilhas devolvem strings) e garante todas as colunas."""
    spec = SCHEMA[table]
    out = {}
    for col in spec["cols"]:
        val = row.get(col, "")
        typ = spec["types"].get(col)
        if typ is not None:
            try:
                val = typ(float(str(val).replace(",", "."))) if val not in ("", None) else None
            except (ValueError, TypeError):
                val = None
        else:
            val = "" if val is None else str(val)
        out[col] = val
    return out
