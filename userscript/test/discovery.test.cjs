// Run with: node --test userscript/test/
const test = require('node:test');
const assert = require('node:assert/strict');
const { installHooks, shouldCapture, redact, isDuolingoUrl } = require('../discovery.user.js');

const JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOjEyMzQ1Nn0.abcdefghijk';
const settle = () => new Promise((r) => setTimeout(r, 20));

function fakeWindow(respond) {
  const calls = [];
  return {
    calls,
    location: { href: 'https://www.duolingo.com/lesson' },
    Request,
    fetch: async (input, init) => {
      calls.push([input, init]);
      return respond(input, init);
    },
  };
}

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { 'content-type': 'application/json; charset=utf-8' } });

test('filters', () => {
  assert.ok(isDuolingoUrl('/2017-06-30/sessions', 'https://www.duolingo.com/learn'));
  assert.ok(isDuolingoUrl('https://goals-api.duolingo.com/x', 'https://www.duolingo.com/'));
  assert.ok(!isDuolingoUrl('https://evil-duolingo.com/x', 'https://www.duolingo.com/'));
  assert.ok(shouldCapture('GET', 'application/json', null));
  assert.ok(!shouldCapture('GET', 'text/html', null));
  assert.ok(shouldCapture('PUT', '', '{"a":1}'));
  assert.ok(!shouldCapture('POST', 'text/plain', 'a=1'));
  assert.equal(redact(`Bearer ${JWT}`), 'Bearer [REDACTED_JWT]');
});

test('fetch: captures JSON exchange and the page still gets its body', async () => {
  const win = fakeWindow(() => json({ challenges: [{ prompt: 'el gato' }] }));
  const captured = [];
  installHooks(win, (e) => captured.push(e));

  const res = await win.fetch('/2017-06-30/sessions', { method: 'post', body: JSON.stringify({ jwt: JWT }) });
  assert.deepEqual(await res.json(), { challenges: [{ prompt: 'el gato' }] });
  await settle();

  assert.equal(captured.length, 1);
  const [e] = captured;
  assert.equal(e.via, 'fetch');
  assert.equal(e.method, 'POST');
  assert.equal(e.status, 200);
  assert.equal(e.request_body, '{"jwt":"[REDACTED_JWT]"}');
  assert.deepEqual(JSON.parse(e.response_body), { challenges: [{ prompt: 'el gato' }] });
  assert.equal(e.response_truncated, false);
});

test('fetch: Request object body is read without consuming the original', async () => {
  const win = fakeWindow(async (input) => json({ echoed: await input.text() }));
  const captured = [];
  installHooks(win, (e) => captured.push(e));

  const req = new Request('https://www.duolingo.com/2017-06-30/sessions/abc', { method: 'PUT', body: '{"done":true}' });
  const res = await win.fetch(req);
  assert.deepEqual(await res.json(), { echoed: '{"done":true}' });
  await settle();
  assert.equal(captured.length, 1);
  assert.equal(captured[0].request_body, '{"done":true}');
});

test('fetch: ignores other hosts and non-JSON GETs, and passes errors through', async () => {
  const win = fakeWindow((input) => {
    if (String(input).includes('fail')) throw new TypeError('network');
    return new Response('<html>', { headers: { 'content-type': 'text/html' } });
  });
  const captured = [];
  installHooks(win, (e) => captured.push(e));

  await win.fetch('https://cdn.example.com/x.json');
  await win.fetch('/learn');
  await assert.rejects(win.fetch('/fail'), TypeError);
  await settle();
  assert.equal(captured.length, 0);
});

test('xhr: captures JSON responses', async () => {
  class FakeXHR {
    constructor() { this.listeners = {}; this.responseType = ''; }
    open() {}
    send() {
      this.status = 200;
      this.responseText = '{"xp":42}';
      this.responseURL = 'https://www.duolingo.com/2017-06-30/users/1';
      setTimeout(() => (this.listeners.loadend || []).forEach((fn) => fn()), 0);
    }
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
    getResponseHeader() { return 'application/json'; }
  }
  const win = { location: { href: 'https://www.duolingo.com/' }, XMLHttpRequest: FakeXHR };
  const captured = [];
  installHooks(win, (e) => captured.push(e));

  const xhr = new win.XMLHttpRequest();
  xhr.open('get', '/2017-06-30/users/1');
  xhr.send();
  await settle();
  assert.equal(captured.length, 1);
  assert.equal(captured[0].via, 'xhr');
  assert.equal(captured[0].method, 'GET');
  assert.equal(captured[0].response_body, '{"xp":42}');
});
