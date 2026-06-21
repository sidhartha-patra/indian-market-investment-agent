// Self-contained streamable-HTTP MCP client for the warm mail server at 127.0.0.1:8970.
// Sends an HTML email via the same SendEmailWithAttachments tool the user's other agents use.
// Usage:
//   node scripts/notify_email.mjs <to> "<subject>" <htmlFile>
// Exits 0 on success, non-zero on failure (callers can ignore failures so a refresh never breaks).
import fs from 'fs';

const URL = process.env.MAIL_MCP_URL || 'http://127.0.0.1:8970/';
let SESSION = null;

function parseBody(text, ct) {
  if (ct && ct.includes('text/event-stream')) {
    let out = null;
    for (const ln of text.split(/\r?\n/)) {
      if (ln.startsWith('data:')) {
        const d = ln.slice(5).trim();
        if (d) { try { out = JSON.parse(d); } catch {} }
      }
    }
    return out;
  }
  try { return JSON.parse(text); } catch { return { _raw: text }; }
}

async function rpc(method, params, isNotification = false) {
  const body = { jsonrpc: '2.0', method };
  if (params !== undefined) body.params = params;
  if (!isNotification) body.id = Math.floor(Math.random() * 1e9);
  const headers = { 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream' };
  if (SESSION) headers['mcp-session-id'] = SESSION;
  const res = await fetch(URL, { method: 'POST', headers, body: JSON.stringify(body) });
  const sid = res.headers.get('mcp-session-id');
  if (sid) SESSION = sid;
  if (isNotification) return null;
  const ct = res.headers.get('content-type') || '';
  return parseBody(await res.text(), ct);
}

async function main() {
  const to = process.argv[2];
  const subject = process.argv[3];
  const htmlFile = process.argv[4];
  if (!to || !subject || !htmlFile) {
    console.error('usage: node notify_email.mjs <to> "<subject>" <htmlFile>');
    process.exit(2);
  }
  const html = fs.readFileSync(htmlFile, 'utf8');
  await rpc('initialize', {
    protocolVersion: '2025-06-18', capabilities: {},
    clientInfo: { name: 'invest-agent-notifier', version: '1.0' },
  });
  await rpc('notifications/initialized', undefined, true);
  const r = await rpc('tools/call', {
    name: 'SendEmailWithAttachments',
    arguments: { to: [to], subject, body: html, contentType: 'HTML' },
  });
  const txt = JSON.stringify(r || {});
  if (/error/i.test(txt) && !/successfully/i.test(txt)) {
    console.error('mail send failed:', txt.slice(0, 300));
    process.exit(1);
  }
  console.log('email sent:', txt.slice(0, 200));
}

main().catch((e) => { console.error('notify_email error:', e.message); process.exit(1); });
