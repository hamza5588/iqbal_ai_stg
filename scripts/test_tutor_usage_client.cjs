const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
const source = readFileSync(resolve(__dirname, '../static/student/js/tutor-usage.js'), 'utf8');

function fixture() {
  let now = 0, focused = true, visible = true, online = true;
  const handlers = {}, timers = [], requests = [];
  class Element {
    constructor(tutor = true) { this.tutor = tutor; this.dataset = { tutorUsage: 'lesson' }; }
    closest() { return this.tutor ? this : null; }
    getClientRects() { return visible ? [{}] : []; }
    getBoundingClientRect() { return { top: 0, left: 0, bottom: 500, right: 500 }; }
  }
  const panel = new Element(), outside = new Element(false);
  function listen(type, callback) { (handlers[type] ||= []).push(callback); }
  const document = { visibilityState: 'visible', hidden: false, hasFocus: () => focused,
    querySelectorAll: () => [panel], addEventListener: listen };
  const context = { document, Element, crypto: { randomUUID }, performance: { now: () => now },
    innerWidth: 1000, innerHeight: 800, addEventListener: listen,
    setInterval(fn, interval) { timers.push({ fn, interval, next: now + interval }); },
    fetch(url, options) {
      requests.push({ url, ...JSON.parse(options.body) });
      return online ? Promise.resolve({ ok: true }) : Promise.reject(new Error('offline'));
    }
  };
  context.window = context;
  vm.runInNewContext(source, context);
  const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
  async function event(type, target = panel, trusted = true) {
    for (const fn of handlers[type] || []) fn({ type, target, isTrusted: trusted });
    await flush();
  }
  async function advance(ms) {
    const end = now + ms;
    while (Math.min(...timers.map(t => t.next)) <= end) {
      now = Math.min(...timers.map(t => t.next));
      for (const timer of timers) if (timer.next === now) { timer.next += timer.interval; timer.fn(); }
      await flush();
    }
    now = end;
  }
  return { requests, panel, outside, event, advance,
    async hide() { document.visibilityState = 'hidden'; document.hidden = true; await event('visibilitychange'); },
    async blur() { focused = false; await event('blur'); },
    async leave() { visible = false; await advance(1000); },
    async offline() { online = false; await event('offline'); },
    async online() { online = true; await event('online'); },
    async suspend(ms) { now += ms; for (const t of timers) { t.next = now + t.interval; t.fn(); } await flush(); }
  };
}

test('no activity or synthetic events cannot start tracking', async () => {
  const f = fixture(); await f.advance(30000); await f.event('pointerdown', f.panel, false); await f.advance(30000);
  assert.equal(f.requests.length, 0);
});
test('active tutor use sends only counters and pauses after two minutes idle', async () => {
  const f = fixture(); await f.event('pointerdown'); await f.advance(180000);
  assert.equal(f.requests[0].total_ms, 0);
  assert.equal(f.requests.at(-1).total_ms, 120000);
  assert.deepEqual(Object.keys(f.requests[0]).sort(), ['seq','session_id','total_ms','url']);
  const count = f.requests.length; await f.advance(60000); assert.equal(f.requests.length, count);
});
for (const pause of ['hide','blur','leave']) test(pause + ' stops accrual', async () => {
  const f = fixture(); await f.event('pointerdown'); await f.advance(15000); await f[pause]();
  const last = f.requests.at(-1).total_ms; await f.advance(60000);
  assert.equal(f.requests.at(-1).total_ms, last);
});
test('interaction elsewhere stops tutor time', async () => {
  const f = fixture(); await f.event('pointerdown'); await f.advance(10000); await f.event('pointerdown', f.outside); await f.advance(60000);
  assert.equal(f.requests.at(-1).total_ms, 10000);
});
test('sleeping browser does not add suspended time', async () => {
  const f = fixture(); await f.event('pointerdown'); await f.advance(15000); await f.suspend(600000);
  assert.equal(f.requests.at(-1).total_ms, 15000);
});
test('offline reconnect creates a fresh baseline', async () => {
  const f = fixture(); await f.event('pointerdown'); await f.advance(15000);
  const original = f.requests[0].session_id;
  await f.offline(); await f.advance(60000); await f.online(); await f.event('pointerdown');
  assert.notEqual(f.requests.at(-1).session_id, original);
  assert.equal(f.requests.at(-1).total_ms, 0);
});
