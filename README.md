# Gestão de Frota — Manutenção, Vencimentos e Desativação Automática

Sistema web (Flask + HTML/Tailwind/JS puro) que calcula, para cada veículo, o status de revisão por **KM** e por **tempo de uso**, com baixa de revisão que avança o ciclo automaticamente. Os dados ficam numa planilha Google usada também pelo **AppSheet**, acessada via **Google Apps Script**. Há um modo local (JSON) para desenvolver sem internet.

## Estrutura

```
app.py                 API Flask + serve o frontend
engine.py              motor de regras (KM atual, idade, status, urgência, KPIs)
normalizer.py          normalização de chassi (regex + Levenshtein) e ciclo de revisões
storage.py             LocalStore (JSON) e GasStore (Apps Script)
schema.py              colunas/tipos de cada tabela (espelhado no Apps Script)
seed.py                carga inicial a partir dos CSVs
gas_integration.js     Apps Script (cole na planilha) — API da planilha/AppSheet
config.py              configuração via variáveis de ambiente
static/                index.html, app.js, styles.css
tests/test_core.py     testes (normalizador, regras, API, RBAC)
input/                 coloque aqui os CSVs (ignorados pelo Git)
data/                  dados locais e credenciais iniciais (ignorados pelo Git)
```

## 1. Instalação e execução local

Requer Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # edite SECRET_KEY e ADMIN_EMAIL
set -a; source .env; set +a        # carrega as variáveis (Windows: defina-as no ambiente)
```

Coloque os arquivos em `input/` e rode a carga inicial:

```bash
python seed.py --frota input/FROTA.csv --abastecimento input/abastecimento.csv
# opcional, quando tiver o histórico:
python seed.py --frota input/FROTA.csv --abastecimento input/abastecimento.csv --revisoes input/ultima_revisao.csv
```

O seed imprime um resumo (inclusive prefixos do abastecimento sem cadastro) e grava as senhas iniciais em `data/usuarios_iniciais.txt` — um gestor global e um operador por filial. **Guarde essas senhas e apague o arquivo.**

```bash
python app.py                      # http://127.0.0.1:5000
python -m unittest discover -s tests -v
```

### Formato dos arquivos de entrada

| Arquivo | Formato |
|---|---|
| `FROTA.csv` (cadastro) | `;` — `Número Ordem;Tipo Chassi;Número Ano Chassi;Local` (cp1252/UTF-8 detectado) |
| `abastecimento.csv` | relatório do ERP em blocos `Veículo : 001/02510 …`; **km_registrada = coluna "Hod. Atual"** |
| `ultima_revisao.csv` (opcional) | colunas `prefixo;data;km` (aceita variações de nome); `km` = hodômetro da revisão |

Para o histórico legado, a meta concluída é o múltiplo do plano mais próximo do KM informado (ex.: revisão a 29.400 km com plano de 30.000 → meta 30.000 concluída → próxima 60.000).

## 2. Google Apps Script + AppSheet

1. Crie uma planilha Google (ou use a do AppSheet) → **Extensões → Apps Script** → cole `gas_integration.js`.
2. Execute `setup()` uma vez (autorize). Ele cria as abas `Usuarios`, `Veiculos`, `Criterios`, `Abastecimentos`, `Revisoes` com cabeçalhos.
3. **Configurações do projeto → Propriedades do script**: crie `SHARED_SECRET` com uma string longa e aleatória.
4. **Implantar → Nova implantação → App da Web** — *Executar como: Eu*; *Acesso: Qualquer pessoa*. A proteção é o `SHARED_SECRET` enviado no corpo da requisição (nunca na URL). Copie a URL `/exec`.
5. No `.env`:
   ```
   STORAGE_BACKEND=gas
   GAS_WEBAPP_URL=https://script.google.com/macros/s/XXXX/exec
   GAS_SHARED_SECRET=<mesmo valor do passo 3>
   ```
6. Rode o `seed.py` novamente: ele grava tudo na planilha (em lotes de 1.500 linhas).
7. **AppSheet**: *Create → App → Start with existing data* e aponte para a mesma planilha. Colunas de identificação (`prefixo`, `nf`…) ficam como texto para preservar zeros à esquerda.

Ao alterar o Apps Script, crie uma **nova versão** da implantação (a URL se mantém). Edições feitas direto na planilha/AppSheet aparecem no sistema em até `CACHE_TTL_SECONDS`.

## 3. Regras de negócio implementadas

| Regra | Implementação |
|---|---|
| **A. RBAC por filial** | Perfil `GLOBAL` vê tudo; `FILIAL` só a sua (veículos, critérios, histórico e baixas). Verificado no servidor em toda rota. |
| **B. Normalização de chassi** | `normalizer.normalizar_chassi`: `LO-916`, `LO916`, `LO 916`, `LO-916 EURO VI` → **`LO-916`**. Levenshtein **sugere** parecidos ao cadastrar, mas não funde sozinho: `OF-1721/59` e `OF-1724L/59` são modelos diferentes a poucas edições. |
| **C. KM atual** | Maior `km_registrada` (Hod. Atual) dos abastecimentos do veículo. Se o maior valor for > 2× o segundo, aparece o aviso ⚠ "KM suspeita" (a regra segue valendo). |
| **D. Status** | Idade = meses entre o ano do chassi (1º jan) e hoje. `Idade > tempo máx.` → **Desativado por tempo** (cinza). `KM atual >= meta` → **Crítico** (vermelho). Faltando ≤ 10% do plano ou ≤ 12 meses → **Alerta** (amarelo). Caso contrário **Regular** (verde). Sem critério → **Sem critério** (cinza claro). |
| **E. Ciclo** | A baixa conclui a meta vigente e define a próxima = meta + plano (30.000 → 60.000 → 90.000). Veículo 2 ciclos atrasado precisa de 2 baixas. Valida nome completo, NF (única por veículo) e data não futura. |

Limiares ajustáveis no `.env` (`ALERTA_KM_PCT`, `ALERTA_TEMPO_MESES`, `KM_DESATIVAR_APOS_EXCESSO`).

### Premissas que você deve revisar

- **Critérios provisórios:** o seed cria, para cada chassi de veículo, plano de 30.000 km e 15 anos, marcados "Provisório" na aba Critérios. Ferramentas, compressores, caixas separadoras etc. ficam sem critério. Ajuste para os valores reais.
- **Sem histórico de revisões**, a meta é assumida como o próximo múltiplo do plano acima do KM atual (marcada com `*`). Por isso, só após importar `ultima_revisao.csv` aparecem veículos "Críticos" por KM.
- **Veículos sem leitura de KM** são avaliados só por tempo e não permitem baixa até haver abastecimento ou histórico.
- **"Desativado/Alerta por KM":** KM ≥ meta aparece como **Crítico** (vermelho, ação imediata). Se quiser que exceder a meta por N km vire **Desativado** (cinza), defina `KM_DESATIVAR_APOS_EXCESSO=N`.

## 4. Versionamento e GitHub

```bash
git init -b main
git add .
git commit -m "feat: sistema de gestão de manutenção de frota"
git remote add origin git@github.com:SEU_USUARIO/frota-manutencao.git
git push -u origin main
```

O `.gitignore` já exclui `.env`, `input/`, `data/` e `*.csv` (dados da frota e credenciais). Para trabalhar em equipe use branches (`git switch -c feat/minha-mudanca`) e Pull Requests. Nunca commite `GAS_SHARED_SECRET` nem `SECRET_KEY`.

## 5. Produção

```bash
gunicorn -w 2 -b 0.0.0.0:8000 "app:create_app()"        # Linux
waitress-serve --port=8000 --call app:create_app        # Windows
```

Coloque atrás de HTTPS (nginx/Caddy), defina um `SECRET_KEY` forte e mantenha poucos workers: o cache e o limite de tentativas de login são por processo.
