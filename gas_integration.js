/**
 * gas_integration.js — Backend de dados no Google Apps Script.
 *
 * A planilha vinculada é a MESMA que o AppSheet usa como fonte de dados:
 * cada aba = uma tabela (Usuarios, Veiculos, Criterios, Abastecimentos, Revisoes).
 * O servidor Flask conversa com este Web App via POST (JSON) — ver storage.py/GasStore.
 *
 * INSTALAÇÃO (resumo; detalhes no README):
 *  1. Planilha Google > Extensões > Apps Script > cole este arquivo.
 *  2. Execute setup() uma vez (cria as abas e cabeçalhos; autorize o script).
 *  3. Propriedades do script > adicione SHARED_SECRET (string longa e aleatória).
 *  4. Implantar > Nova implantação > App da Web:
 *       Executar como: Eu | Quem tem acesso: Qualquer pessoa
 *     (o acesso é protegido pelo SHARED_SECRET enviado no corpo da requisição).
 *  5. Copie a URL /exec para GAS_WEBAPP_URL e o segredo para GAS_SHARED_SECRET no .env.
 */

// Espelho de schema.py. text = colunas gravadas como TEXTO (preserva "02510", "001"...).
const SCHEMA = {
  Usuarios:       { cols: ['email', 'nome', 'senha_hash', 'perfil', 'filial'],
                    text: ['email', 'nome', 'senha_hash', 'perfil', 'filial'] },
  Veiculos:       { cols: ['prefixo', 'tipo_chassi_original', 'tipo_chassi', 'ano_chassi', 'filial', 'criado_em'],
                    text: ['prefixo', 'tipo_chassi_original', 'tipo_chassi', 'filial', 'criado_em'] },
  Criterios:      { cols: ['tipo_chassi', 'filial', 'plano_km', 'tempo_max_meses', 'revisar', 'atualizado_em'],
                    text: ['tipo_chassi', 'filial', 'revisar', 'atualizado_em'] },
  Abastecimentos: { cols: ['data', 'prefixo', 'empresa', 'km_registrada', 'km_rodado', 'litros', 'posto'],
                    text: ['data', 'prefixo', 'empresa', 'posto'] },
  Revisoes:       { cols: ['id', 'prefixo', 'data', 'meta_km_concluida', 'proxima_meta_km', 'km_no_momento',
                           'responsavel', 'nf', 'observacoes', 'origem', 'registrado_por', 'criado_em'],
                    text: ['id', 'prefixo', 'data', 'responsavel', 'nf', 'observacoes', 'origem', 'registrado_por', 'criado_em'] },
};

/** Execute UMA vez no editor: cria abas/cabeçalhos. Não apaga dados existentes. */
function setup() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  Object.keys(SCHEMA).forEach(function (name) {
    const sh = ss.getSheetByName(name) || ss.insertSheet(name);
    const cols = SCHEMA[name].cols;
    sh.getRange(1, 1, 1, cols.length).setValues([cols]).setFontWeight('bold').setBackground('#ECFDF5');
    sh.setFrozenRows(1);
    applyTextFormat_(sh, name);
  });
  if (!PropertiesService.getScriptProperties().getProperty('SHARED_SECRET')) {
    Logger.log('ATENÇÃO: defina a propriedade de script SHARED_SECRET antes de implantar.');
  }
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    const req = JSON.parse(e.postData.contents);
    const secret = PropertiesService.getScriptProperties().getProperty('SHARED_SECRET');
    if (!secret || !safeEqual_(String(req.secret || ''), secret)) return json_({ ok: false, error: 'unauthorized' });
    if (!SCHEMA[req.table]) return json_({ ok: false, error: 'tabela inválida' });

    // Leitura não precisa de lock; escritas são serializadas.
    if (req.action === 'list') return json_({ ok: true, rows: readAll_(req.table) });
    lock.waitLock(25000);
    switch (req.action) {
      case 'upsert':      return json_({ ok: true, row: upsert_(req.table, req.row, req.key) });
      case 'append_many': return json_({ ok: true, count: appendMany_(req.table, req.rows) });
      case 'replace_all': return json_({ ok: true, count: replaceAll_(req.table, req.rows) });
      case 'delete':      return json_({ ok: true, deleted: delete_(req.table, req.ref, req.key) });
      default:            return json_({ ok: false, error: 'ação inválida' });
    }
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    try { lock.releaseLock(); } catch (_) {}
  }
}

/** Teste rápido no navegador: abre a URL /exec e deve responder {"ok":true,...}. */
function doGet() { return json_({ ok: true, service: 'frota-gas', tables: Object.keys(SCHEMA) }); }

// ------------------------------- operações ---------------------------------
function sheet_(table) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(table);
  if (!sh) throw new Error('Aba não encontrada: ' + table + ' (execute setup()).');
  return sh;
}

function readAll_(table) {
  const sh = sheet_(table), cols = SCHEMA[table].cols, last = sh.getLastRow();
  if (last < 2) return [];
  const values = sh.getRange(2, 1, last - 1, cols.length).getValues();
  return values.map(function (r) {
    const o = {};
    cols.forEach(function (c, i) { o[c] = cell_(r[i]); });
    return o;
  });
}

function upsert_(table, row, key) {
  const sh = sheet_(table), cols = SCHEMA[table].cols, last = sh.getLastRow();
  const out = cols.map(function (c) { return row[c] === undefined || row[c] === null ? '' : row[c]; });
  if (last >= 2) {
    const keyIdx = key.map(function (k) { return cols.indexOf(k); });
    const data = sh.getRange(2, 1, last - 1, cols.length).getValues();
    for (let i = 0; i < data.length; i++) {
      if (keyIdx.every(function (ix, n) { return String(data[i][ix]) === String(row[key[n]]); })) {
        sh.getRange(i + 2, 1, 1, cols.length).setValues([out]);
        return row;
      }
    }
  }
  appendMany_(table, [row]);
  return row;
}

function appendMany_(table, rows) {
  if (!rows || !rows.length) return 0;
  const sh = sheet_(table), cols = SCHEMA[table].cols;
  const start = Math.max(sh.getLastRow(), 1) + 1;
  const values = rows.map(function (r) {
    return cols.map(function (c) { return r[c] === undefined || r[c] === null ? '' : r[c]; });
  });
  applyTextFormat_(sh, table, start, values.length);   // formata ANTES de gravar
  sh.getRange(start, 1, values.length, cols.length).setValues(values);
  return values.length;
}

function replaceAll_(table, rows) {
  const sh = sheet_(table), cols = SCHEMA[table].cols;
  if (sh.getLastRow() > 1) sh.getRange(2, 1, sh.getLastRow() - 1, cols.length).clearContent();
  return appendMany_(table, rows || []);
}

function delete_(table, ref, key) {
  const sh = sheet_(table), cols = SCHEMA[table].cols, last = sh.getLastRow();
  if (last < 2) return false;
  const keyIdx = key.map(function (k) { return cols.indexOf(k); });
  const data = sh.getRange(2, 1, last - 1, cols.length).getValues();
  for (let i = data.length - 1; i >= 0; i--) {
    if (keyIdx.every(function (ix, n) { return String(data[i][ix]) === String(ref[key[n]]); })) {
      sh.deleteRow(i + 2);
      return true;
    }
  }
  return false;
}

// -------------------------------- utilitários -------------------------------
function applyTextFormat_(sh, table, startRow, numRows) {
  const spec = SCHEMA[table], start = startRow || 2, n = numRows || Math.max(sh.getMaxRows() - 1, 1);
  spec.text.forEach(function (c) {
    sh.getRange(start, spec.cols.indexOf(c) + 1, n, 1).setNumberFormat('@');
  });
}

function cell_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  return v;
}

function safeEqual_(a, b) {                       // comparação em tempo ~constante
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
