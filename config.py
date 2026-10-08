"""Configuração central (variáveis de ambiente com valores padrão seguros)."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


# --- Persistência -----------------------------------------------------------
# "local": arquivos JSON em ./data (desenvolvimento / testes)
# "gas"  : Google Apps Script Web App (planilha ligada ao AppSheet)
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
LOCAL_DATA_DIR = os.getenv("LOCAL_DATA_DIR", os.path.join(BASE_DIR, "data"))
GAS_WEBAPP_URL = os.getenv("GAS_WEBAPP_URL", "")
GAS_SHARED_SECRET = os.getenv("GAS_SHARED_SECRET", "")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))

# --- Segurança --------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "troque-esta-chave-em-producao")
TOKEN_MAX_AGE_SECONDS = int(os.getenv("TOKEN_MAX_AGE_SECONDS", str(8 * 3600)))

# --- Regras de negócio (ajustáveis sem mexer no código) ---------------------
# Alerta preventivo por KM: faltando <= X% do plano para a meta.
ALERTA_KM_PCT = _float("ALERTA_KM_PCT", 0.10)
# Alerta preventivo por tempo: faltando <= N meses para o tempo ativo máximo.
ALERTA_TEMPO_MESES = int(os.getenv("ALERTA_TEMPO_MESES", "12"))
# Se > 0, veículo que ultrapassar a meta em mais que N km passa a "DESATIVADO"
# (cinza) por limite de uso. 0 = desligado (fica apenas CRÍTICO/vermelho).
KM_DESATIVAR_APOS_EXCESSO = int(os.getenv("KM_DESATIVAR_APOS_EXCESSO", "0"))

# Valores PROVISÓRIOS usados pelo seed ao criar critérios. Revise na tela.
DEFAULT_PLANO_KM = int(os.getenv("DEFAULT_PLANO_KM", "30000"))
DEFAULT_TEMPO_MAX_ANOS = int(os.getenv("DEFAULT_TEMPO_MAX_ANOS", "15"))
