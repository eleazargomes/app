"""API Flask + servidor do frontend. Execute: python app.py"""
import logging
import re
import threading
import time
import uuid
from datetime import date, datetime
from functools import wraps

from flask import Flask, g, jsonify, request, send_from_directory
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash

import config
import engine
from normalizer import (meta_vigente, normalizar_chassi, normalizar_prefixo,
                        proxima_meta, sugerir_similares)
from storage import get_store

log = logging.getLogger("frota")
NF_RE = re.compile(r"^[A-Za-z0-9./\-]{1,20}$")


def create_app(store=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False
    store = store or get_store()
    signer = URLSafeTimedSerializer(config.SECRET_KEY, salt="frota-auth")
    baixa_lock = threading.Lock()
    tentativas = {}                               # rate-limit simples do login

    if config.SECRET_KEY == "troque-esta-chave-em-producao":
        log.warning("SECRET_KEY padrão em uso — defina uma chave própria no .env")

    # ------------------------------------------------------------------ auth
    def erro(msg, status=400, **extra):
        return jsonify({"erro": msg, **extra}), status

    def requer_login(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            try:
                email = signer.loads(token, max_age=config.TOKEN_MAX_AGE_SECONDS)["email"]
            except (BadSignature, SignatureExpired, KeyError):
                return erro("Sessão inválida ou expirada.", 401)
            user = next((u for u in store.list("Usuarios") if u["email"] == email), None)
            if not user:
                return erro("Usuário não encontrado.", 401)
            g.user = user
            return fn(*a, **kw)
        return wrapper

    def global_user():
        return g.user["perfil"] == "GLOBAL"

    def pode_ver(filial):
        return global_user() or filial == g.user["filial"]

    def veiculos_do_escopo(filial_filtro=None):
        vs = [v for v in store.list("Veiculos") if pode_ver(v["filial"])]
        if filial_filtro:
            vs = [v for v in vs if v["filial"] == filial_filtro]
        return vs

    def avaliar(veiculos):
        return engine.avaliar_frota(veiculos, store.list("Criterios"),
                                    store.list("Abastecimentos"), store.list("Revisoes"))

    # ----------------------------------------------------------------- rotas
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/health")
    def health():
        return {"ok": True, "backend": config.STORAGE_BACKEND}

    @app.post("/api/login")
    def login():
        d = request.get_json(silent=True) or {}
        email, senha = str(d.get("email", "")).strip().lower(), str(d.get("senha", ""))
        chave = (email, request.remote_addr)
        janela = [t for t in tentativas.get(chave, []) if time.time() - t < 60]
        if len(janela) >= 5:
            return erro("Muitas tentativas. Aguarde 1 minuto.", 429)
        user = next((u for u in store.list("Usuarios") if u["email"].lower() == email), None)
        if not user or not check_password_hash(user["senha_hash"], senha):
            tentativas[chave] = janela + [time.time()]
            return erro("E-mail ou senha incorretos.", 401)
        tentativas.pop(chave, None)
        return {"token": signer.dumps({"email": user["email"]}),
                "usuario": {k: user[k] for k in ("email", "nome", "perfil", "filial")}}

    @app.get("/api/me")
    @requer_login
    def me():
        filiais = sorted({v["filial"] for v in veiculos_do_escopo()})
        return {"usuario": {k: g.user[k] for k in ("email", "nome", "perfil", "filial")},
                "filiais": filiais, "status": engine.STATUS}

    # --------------------------------------------------------- painel / KPIs
    @app.get("/api/veiculos")
    @requer_login
    def listar_veiculos():
        filial = request.args.get("filial") or None
        if filial and not pode_ver(filial):
            return erro("Filial fora do seu escopo.", 403)
        av = avaliar(veiculos_do_escopo(filial))
        av.sort(key=lambda a: a["urgencia"], reverse=True)   # ranking por urgência
        return {"kpis": engine.kpis(av), "veiculos": av}

    @app.get("/api/veiculos/<prefixo>/historico")
    @requer_login
    def historico(prefixo):
        p = normalizar_prefixo(prefixo)
        v = next((v for v in store.list("Veiculos") if v["prefixo"] == p), None)
        if not v or not pode_ver(v["filial"]):
            return erro("Veículo não encontrado.", 404)
        h = [r for r in store.list("Revisoes") if r["prefixo"] == p]
        h.sort(key=lambda r: (r["data"], r["criado_em"]), reverse=True)
        return {"veiculo": v, "revisoes": h}

    @app.post("/api/veiculos")
    @requer_login
    def cadastrar_veiculo():
        d = request.get_json(silent=True) or {}
        prefixo = normalizar_prefixo(d.get("prefixo"))
        tipo_orig = str(d.get("tipo_chassi", "")).strip()
        tipo = normalizar_chassi(tipo_orig)
        filial = str(d.get("filial", "")).strip() if global_user() else g.user["filial"]
        try:
            ano = int(d.get("ano_chassi"))
        except (TypeError, ValueError):
            return erro("Ano do chassi inválido.")
        if not prefixo or len(prefixo) > 20:
            return erro("Informe a placa/prefixo (até 20 caracteres).")
        if not tipo:
            return erro("Informe o tipo de chassi.")
        if not filial:
            return erro("Informe a filial/unidade.")
        if not (1950 <= ano <= date.today().year + 1):
            return erro("Ano do chassi fora do intervalo permitido.")
        with store.write_lock:
            if any(v["prefixo"] == prefixo for v in store.list("Veiculos")):
                return erro(f"O prefixo {prefixo} já está cadastrado.", 409)
            conhecidos = {c["tipo_chassi"] for c in store.list("Criterios")} | \
                         {v["tipo_chassi"] for v in store.list("Veiculos")}
            sugestoes = [] if tipo in conhecidos else sugerir_similares(tipo, conhecidos)
            if sugestoes and not d.get("confirmar_chassi_novo"):
                return erro("Chassi novo, parecido com já cadastrado(s).", 409,
                            sugestoes=sugestoes, codigo="CHASSI_PARECIDO")
            store.append("Veiculos", {"prefixo": prefixo, "tipo_chassi_original": tipo_orig,
                                      "tipo_chassi": tipo, "ano_chassi": ano, "filial": filial,
                                      "criado_em": datetime.now().isoformat(timespec="seconds")})
        tem_criterio = engine.achar_criterio(store.list("Criterios"), tipo, filial) is not None
        return {"ok": True, "prefixo": prefixo, "tipo_chassi": tipo,
                "aviso": None if tem_criterio else "Sem critério para este chassi — cadastre em Critérios."}, 201

    # -------------------------------------------------------------- critérios
    @app.get("/api/chassi/normalizar")
    @requer_login
    def api_normalizar():
        valor = request.args.get("valor", "")
        conhecidos = {c["tipo_chassi"] for c in store.list("Criterios")} | \
                     {v["tipo_chassi"] for v in store.list("Veiculos")}
        n = normalizar_chassi(valor)
        return {"normalizado": n, "existe": n in conhecidos,
                "sugestoes": sugerir_similares(valor, conhecidos)}

    @app.get("/api/chassis")
    @requer_login
    def tipos_chassi():
        return {"tipos": sorted({v["tipo_chassi"] for v in veiculos_do_escopo()} |
                                {c["tipo_chassi"] for c in store.list("Criterios")})}

    @app.get("/api/criterios")
    @requer_login
    def listar_criterios():
        cs = [c for c in store.list("Criterios") if c["filial"] in ("*", "") or pode_ver(c["filial"])]
        return {"criterios": sorted(cs, key=lambda c: (c["tipo_chassi"], c["filial"]))}

    @app.post("/api/criterios")
    @requer_login
    def salvar_criterio():
        d = request.get_json(silent=True) or {}
        tipo = normalizar_chassi(d.get("tipo_chassi"))
        filial = str(d.get("filial") or "*").strip()
        if not global_user():
            filial = g.user["filial"]           # operador só edita a própria filial
        try:
            plano = int(str(d.get("plano_km")).replace(".", ""))
            valor = float(d.get("tempo_max"))
        except (TypeError, ValueError):
            return erro("Plano de KM e tempo máximo devem ser numéricos.")
        meses = round(valor * (12 if d.get("unidade", "anos") == "anos" else 1))
        if not tipo or plano <= 0 or meses <= 0:
            return erro("Informe chassi, plano de KM (>0) e tempo máximo (>0).")
        if filial not in ("*",) and not any(v["filial"] == filial for v in store.list("Veiculos")):
            return erro("Filial desconhecida.")
        c = store.upsert("Criterios", {"tipo_chassi": tipo, "filial": filial, "plano_km": plano,
                                       "tempo_max_meses": meses, "revisar": "NAO",
                                       "atualizado_em": datetime.now().isoformat(timespec="seconds")})
        return c, 200

    @app.delete("/api/criterios")
    @requer_login
    def excluir_criterio():
        tipo = normalizar_chassi(request.args.get("tipo_chassi"))
        filial = request.args.get("filial") or "*"
        if not global_user() and filial != g.user["filial"]:
            return erro("Você só pode excluir critérios da sua filial.", 403)
        if not store.delete("Criterios", {"tipo_chassi": tipo, "filial": filial}):
            return erro("Critério não encontrado.", 404)
        return {"ok": True}

    # ------------------------------------------------------ baixa de revisão
    @app.post("/api/revisoes")
    @requer_login
    def baixar_revisao():
        d = request.get_json(silent=True) or {}
        prefixo = normalizar_prefixo(d.get("prefixo"))
        resp = re.sub(r"\s+", " ", str(d.get("responsavel", ""))).strip()
        nf = str(d.get("nf", "")).strip()
        data = str(d.get("data", "")).strip()
        obs = str(d.get("observacoes", "")).strip()[:1000]
        if len(resp.split(" ")) < 2 or len(resp) < 5:
            return erro("Informe o nome completo do responsável.")
        if not NF_RE.match(nf):
            return erro("Número da NF inválido (use letras, números, . / -).")
        try:
            dt = date.fromisoformat(data)
        except ValueError:
            return erro("Data inválida.")
        if dt > date.today():
            return erro("A data de realização não pode estar no futuro.")

        with baixa_lock, store.write_lock:          # evita duas baixas simultâneas
            v = next((v for v in store.list("Veiculos") if v["prefixo"] == prefixo), None)
            if not v or not pode_ver(v["filial"]):
                return erro("Veículo não encontrado.", 404)
            crit = engine.achar_criterio(store.list("Criterios"), v["tipo_chassi"], v["filial"])
            if not crit:
                return erro("Veículo sem critério de revisão cadastrado.", 422)
            revs = [r for r in store.list("Revisoes") if r["prefixo"] == prefixo]
            if any(r["nf"].lower() == nf.lower() for r in revs):
                return erro(f"A NF {nf} já foi usada neste veículo.", 409)
            kms = engine.km_atual_por_veiculo(
                [a for a in store.list("Abastecimentos") if a["prefixo"] == prefixo])
            km = kms.get(prefixo, {}).get("km")
            ultima = max(revs, key=lambda r: r.get("proxima_meta_km") or 0, default=None)
            if km is None and not ultima:
                return erro("Sem leitura de KM nem histórico: registre um abastecimento "
                            "ou importe a última revisão antes da baixa.", 422)
            meta, _ = meta_vigente(crit["plano_km"], km or 0, ultima)
            prox = proxima_meta(crit["plano_km"], meta)
            store.append("Revisoes", {
                "id": uuid.uuid4().hex[:12], "prefixo": prefixo, "data": data,
                "meta_km_concluida": meta, "proxima_meta_km": prox, "km_no_momento": km,
                "responsavel": resp, "nf": nf, "observacoes": obs, "origem": "APP",
                "registrado_por": g.user["email"],
                "criado_em": datetime.now().isoformat(timespec="seconds")})
        novo = avaliar([v])[0]
        return {"ok": True, "meta_concluida": meta, "proxima_meta": prox, "veiculo": novo}, 201

    return app


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO)
    create_app().run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")),
                     debug=os.getenv("FLASK_DEBUG") == "1")
