// Browser checks for tools/qa/chat_server.py; writes screenshots and results.json to the given folder.
const { chromium } = require('playwright');
const out = process.argv[2];
require('fs').mkdirSync(out, { recursive: true });
const results = [];
const check = (name, ok, detail='') => { results.push({name, ok: !!ok, detail}); console.log((ok?'PASS':'FAIL')+' '+name+(detail?' · '+detail:'')); };

async function page(browser, url, viewport) {
  const p = await browser.newPage({ viewport });
  p.errors = [];
  p.on('pageerror', e => p.errors.push(String(e)));
  p.on('console', m => { if (m.type() === 'error' && !(m.location().url || '').endsWith('/api/session')) p.errors.push(m.text() + ' @ ' + (m.location().url || '')); });
  await p.goto(url);
  await p.waitForSelector('.summary-card', { timeout: 60000 });
  return p;
}
const lastAnswer = p => p.locator('.chat-msg.assistant .bubble').last();
async function waitAnswer(p, count) {
  await p.waitForFunction(n => document.querySelectorAll('.chat-msg.assistant .bubble:not(.typing)').length >= n && !document.querySelector('.typing'), count, { timeout: 30000 });
}

(async () => {
  const browser = await chromium.launch();
  // A. Local dashboard without AI: deterministic chat, critical path, saturation, quality.
  const a = await page(browser, 'http://127.0.0.1:8766/', { width: 1440, height: 900 });
  await a.click('#chat-toggle');
  check('A chat opens', await a.isVisible('#chat-panel'));
  check('A deterministic mode', (await a.textContent('#chat-mode')).includes('deterministica'));
  const first = a.locator('[data-chat-suggestion]').first();
  const suggestion = await first.textContent();
  await first.click();
  await waitAnswer(a, 1);
  const explained = await lastAnswer(a).innerText();
  check('A why-late answer uses critical path', /Percorso critico/.test(explained) && /(attende|parte|fissat|ritroso|completata)/.test(explained), suggestion);
  await a.fill('#chat-input', 'Quali macchine sono più cariche?');
  await a.press('#chat-input', 'Enter');
  await waitAnswer(a, 2);
  check('A loads answer', /Utilizzo delle risorse/.test(await lastAnswer(a).innerText()));
  await a.screenshot({ path: out + '/a-chat.png' });
  check('A chat beside content', await a.evaluate(() => document.querySelector('.main').getBoundingClientRect().right <= document.querySelector('#chat-panel').getBoundingClientRect().left + 1));
  await a.click('#chat-close');
  await a.selectOption('#resource-kind', 'operator');
  await a.locator('[data-task]').first().click();
  await a.waitForSelector('[data-explain-order]');
  await a.click('[data-explain-order]');
  await a.waitForSelector('.critical-path li', { timeout: 30000 });
  check('A inspector critical path', await a.locator('.critical-path li').count() > 0);
  await a.screenshot({ path: out + '/a-inspector.png' });
  await a.click('[data-view="loads"]');
  await a.click('[data-unit="percent"]');
  await a.waitForFunction(() => [...document.querySelectorAll('.heat-cell')].some(c => c.textContent.endsWith('%')), null, { timeout: 30000 });
  check('A saturation cells', await a.locator('.heat-cell', { hasText: '%' }).count() > 0);
  await a.screenshot({ path: out + '/a-loads.png' });
  await a.click('[data-view="assistant"]');
  await a.waitForSelector('.quality-tile', { timeout: 30000 });
  check('A quality tiles', await a.locator('.quality-tile').count() === 4);
  await a.fill('#assistant-input', 'Quali attività non sono pianificate?');
  await a.click('#assistant-form button[type=submit]');
  await waitAnswer(a, 3);
  check('A assistant page routes to chat', await a.isVisible('#chat-panel'));
  await a.screenshot({ path: out + '/a-assistant.png' });
  await a.reload(); await a.waitForSelector('.summary-card');
  await a.click('#chat-toggle');
  check('A history survives reload', await a.locator('.chat-msg.user').count() === 3);
  await a.click('#chat-reset');
  check('A new chat clears history', await a.locator('.chat-msg.user').count() === 0);
  check('A no console errors', a.errors.length === 0, a.errors.join(' | '));

  // B. Secure local-user Control Room with a simulated provider: AI answer and applied proposal.
  const b = await page(browser, 'http://127.0.0.1:8767/', { width: 1440, height: 900 });
  await b.click('#chat-toggle');
  check('B AI mode', (await b.textContent('#chat-mode')).includes('AI collegata'));
  await b.fill('#chat-input', 'Perché ORD011 è in ritardo?');
  await b.press('#chat-input', 'Enter');
  await waitAnswer(b, 1);
  const ai = await lastAnswer(b).innerText();
  check('B AI answer from explain_order', /Secondo il percorso critico/.test(ai) && /explain_order/.test(ai));
  check('B formatting escaped and bold rendered', await lastAnswer(b).locator('strong').count() > 0);
  await b.fill('#chat-input', 'Simula un fermo della prima macchina');
  await b.press('#chat-input', 'Enter');
  await waitAnswer(b, 2);
  await b.waitForSelector('[data-chat-apply]');
  await b.screenshot({ path: out + '/b-proposal.png' });
  await b.click('[data-chat-apply]');
  await b.waitForSelector('.comparison-card', { timeout: 60000 });
  await b.waitForSelector('.chat-proposal .status-pill', { timeout: 30000 });
  check('B proposal applied as verified simulation', /Applicata/.test(await b.textContent('.chat-proposal')) && await b.isVisible('#reset-button'));
  await b.screenshot({ path: out + '/b-applied.png' });
  check('B no console errors', b.errors.length === 0, b.errors.join(' | '));

  // C. Phone width: panel fits, no horizontal page scroll.
  const c = await page(browser, 'http://127.0.0.1:8766/', { width: 390, height: 844 });
  await c.click('#chat-toggle');
  const width = await c.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  const panel = await c.locator('#chat-panel').boundingBox();
  check('C phone panel fits', panel.width <= 390 && width[0] <= width[1], JSON.stringify({width, panel: panel.width}));
  await c.screenshot({ path: out + '/c-phone.png' });
  check('C no console errors', c.errors.length === 0, c.errors.join(' | '));

  await browser.close();
  require('fs').writeFileSync(out + '/results.json', JSON.stringify(results, null, 1));
  process.exit(results.every(r => r.ok) ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });
