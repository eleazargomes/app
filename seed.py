"""
Carga inicial a partir dos CSVs legados.

Uso:
  python seed.py --frota input/FROTA.csv --abastecimento input/abastecimento.csv \
                 [--revisoes input/ultima_revisao.csv] [--reset]

Arquivos:
  FROTA.csv           ; Número Ordem;Tipo Chassi;Número Ano Chassi;Local
  abastecimento.csv   ; relatório do ERP em blocos "Veículo : 001/02510 ..."
                        (km_registrada = coluna "Hod. Atual")
  ultima_revisao.csv  ; OPCIONAL. Colunas (aceita variações): prefixo;data;km
"""
import argparse
import csv
import io
import os
import re
import secrets
import sys
import unicodedata
from datetime import date, datetime

import config
from normalizer import normalizar_chassi, normalizar_prefixo
from storage import get_store

# Tipos do cadastro que não são veículos rodantes: ficam SEM critério de KM/tempo.
NAO_VEICULO = {"FERRAMENTAS", "COMPRESSOR", "CAIXA SEPARADORA", "ASSENTADOR DE TALAO",
               "CABINE DE PINTURA", "03 GERADO DO EXPORTA", "UNIVALE", "APOIO"}


def ler_texto(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Não foi possível decodificar {path}")


def num_br(s):
    """'1.280,29' -> 1280.29 | '458.705' -> 458705.0 | '' -> None"""
    s = (s or "").strip().strip('"')
    if not s:
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def data_br(s):
    s = (s or "").strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def ler_frota(path):
    rows, rejeitados = [], []
    leitor = csv.DictReader(io.StringIO(ler_texto(path)), delimiter=";")
    for r in leitor:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        prefixo = normalizar_prefixo(r.get("Número Ordem"))
        try:
            ano = int(r.get("Número Ano Chassi", ""))
        except ValueError:
            ano = None
        if not prefixo or not ano or not (1950 <= ano <= date.today().year + 1):
            rejeitados.append((r, "prefixo/ano inválido"))
            continue
        rows.append({"prefixo": prefixo,
                     "tipo_chassi_original": r.get("Tipo Chassi", ""),
                     "tipo_chassi": normalizar_chassi(r.get("Tipo Chassi", "")),
                     "ano_chassi": ano, "filial": r.get("Local", ""),
                     "criado_em": datetime.now().isoformat(timespec="seconds")})
    return rows, rejeitados


_BLOCO = re.compile(r"^Ve[ií]culo\s*:\s*(\S+)")


def ler_abastecimentos(path):
    """Máquina de estados sobre o relatório em blocos. Retorna lista de dicts."""
    atual, data_ant, out = None, None, []
    for linha in ler_texto(path).splitlines():
        m = _BLOCO.match(_sem_acento(linha))
        if m:
            empresa, _, pref = m.group(1).rpartition("/")
            atual = (empresa, normalizar_prefixo(pref))
            data_ant = None
            continue
        if not atual or not linha.startswith('"') or linha.startswith('"  ";"  ";"  "'):
            continue                      # cabeçalhos, totais e linhas em branco
        cols = next(csv.reader([linha], delimiter=";"))
        if len(cols) < 17:
            continue
        data = data_br(cols[0]) or data_ant   # linha sem data = mesmo dia anterior
        km = num_br(cols[2])                  # Hod. Atual
        if km is None:
            continue
        data_ant = data
        out.append({"data": data or "", "prefixo": atual[1], "empresa": atual[0],
                    "km_registrada": int(km), "km_rodado": int(num_br(cols[3]) or 0),
                    "litros": num_br(cols[4]) or 0.0, "posto": cols[14].strip()})
    return out


def ler_ultimas_revisoes(path, criterios_por_chassi, veiculos):
    """Converte 'ultima_revisao.csv' em registros de Revisoes (origem LEGADO)."""
    texto = ler_texto(path)
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";" if texto.count(";") > texto.count(",") else ",")
    def pick(row, *nomes):
        for k, v in row.items():
            if _sem_acento(k or "").strip().lower() in nomes:
                return (v or "").strip()
        return ""
    por_prefixo = {v["prefixo"]: v for v in veiculos}
    out = []
    for i, r in enumerate(leitor, 1):
        pref = normalizar_prefixo(pick(r, "prefixo", "frota", "numero ordem", "veiculo", "placa"))
        v = por_prefixo.get(pref)
        km = num_br(pick(r, "km", "km_revisao", "km revisao", "hodometro", "km_registrada"))
        if not v or km is None or v["tipo_chassi"] not in criterios_por_chassi:
            continue
        plano = criterios_por_chassi[v["tipo_chassi"]]
        meta = max(plano, round(km / plano) * plano)
        out.append({"id": f"LEG-{pref}-{i}", "prefixo": pref,
                    "data": data_br(pick(r, "data", "data_revisao", "data revisao")) or "",
                    "meta_km_concluida": meta, "proxima_meta_km": meta + plano,
                    "km_no_momento": int(km), "responsavel": "(legado)", "nf": "",
                    "observacoes": "Importado de ultima_revisao.csv", "origem": "LEGADO",
                    "registrado_por": "seed", "criado_em": datetime.now().isoformat(timespec="seconds")})
    return out


def criar_usuarios(store, filiais, admin_email):
    from werkzeug.security import generate_password_hash
    existentes = {u["email"] for u in store.list("Usuarios")}
    novos, linhas = [], []
    def add(email, nome, perfil, filial):
        if email in existentes:
            return
        senha = secrets.token_urlsafe(8)
        novos.append({"email": email, "nome": nome, "senha_hash": generate_password_hash(senha),
                      "perfil": perfil, "filial": filial})
        linhas.append(f"{email}\t{senha}\t{perfil}\t{filial}")
    add(admin_email, "Gestor Global", "GLOBAL", "*")
    for f in sorted(filiais):
        slug = re.sub(r"[^a-z0-9]+", ".", _sem_acento(f).lower()).strip(".")
        add(f"{slug}@frota.local", f"Operador {f}", "FILIAL", f)
    for u in novos:
        store.append("Usuarios", u)
    return linhas


def run_seed(store, frota, abastecimento, revisoes=None, admin_email="admin@frota.local", reset=False):
    veiculos, rejeitados = ler_frota(frota)
    abast = ler_abastecimentos(abastecimento)
    conhecidos = {v["prefixo"] for v in veiculos}
    orfaos = sorted({a["prefixo"] for a in abast} - conhecidos)
    abast = [a for a in abast if a["prefixo"] in conhecidos]

    tipos = sorted({v["tipo_chassi"] for v in veiculos})
    agora = datetime.now().isoformat(timespec="seconds")
    criterios = [{"tipo_chassi": t, "filial": "*", "plano_km": config.DEFAULT_PLANO_KM,
                  "tempo_max_meses": config.DEFAULT_TEMPO_MAX_ANOS * 12,
                  "revisar": "SIM", "atualizado_em": agora}
                 for t in tipos if t not in NAO_VEICULO]
    if reset or not store.list("Criterios"):
        store.replace_all("Criterios", criterios)
    store.replace_all("Veiculos", veiculos)
    store.replace_all("Abastecimentos", abast)
    if reset or not store.list("Revisoes"):
        store.replace_all("Revisoes", [])
    n_leg = 0
    if revisoes:
        plano = {c["tipo_chassi"]: c["plano_km"] for c in criterios}
        leg = ler_ultimas_revisoes(revisoes, plano, veiculos)
        store.replace_all("Revisoes", leg)
        n_leg = len(leg)
    senhas = criar_usuarios(store, {v["filial"] for v in veiculos}, admin_email)
    return {"veiculos": len(veiculos), "rejeitados": len(rejeitados),
            "abastecimentos": len(abast), "prefixos_sem_cadastro": orfaos,
            "tipos_chassi": len(tipos), "criterios_provisorios": len(criterios),
            "revisoes_legado": n_leg, "credenciais": senhas}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frota", default="input/FROTA.csv")
    ap.add_argument("--abastecimento", default="input/abastecimento.csv")
    ap.add_argument("--revisoes", default=None)
    ap.add_argument("--admin-email", default=os.getenv("ADMIN_EMAIL", "admin@frota.local"))
    ap.add_argument("--reset", action="store_true", help="recria critérios e revisões")
    a = ap.parse_args()
    for p in (a.frota, a.abastecimento):
        if not os.path.exists(p):
            sys.exit(f"Arquivo não encontrado: {p}")
    if a.revisoes and not os.path.exists(a.revisoes):
        print(f"Aviso: {a.revisoes} não existe; seguindo sem histórico de revisões.")
        a.revisoes = None
    r = run_seed(get_store(), a.frota, a.abastecimento, a.revisoes, a.admin_email, a.reset)
    cred = r.pop("credenciais")
    print("Carga concluída:")
    for k, v in r.items():
        print(f"  {k}: {v if not isinstance(v, list) else (len(v), v[:12])}")
    if cred:
        path = os.path.join(config.BASE_DIR, "data", "usuarios_iniciais.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("email\tsenha\tperfil\tfilial\n" + "\n".join(cred) + "\n")
        print(f"\nCredenciais iniciais gravadas em {path} (arquivo ignorado pelo Git).")
        print("Troque as senhas após o primeiro acesso.")


if __name__ == "__main__":
    main()
