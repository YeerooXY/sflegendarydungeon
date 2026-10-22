const {test} = require('node:test');
const assert = require('node:assert/strict');
const {gameOrigin, navigationAllowed, importProfiles, validateSaved} = require('../profiles.cjs');

test('imports isolated identities, deduplicates, and preserves saved sessions', () => {
  const first = importProfiles({accounts: [{label: 'Personal', world: 's1.sfgame.eu'}]});
  const second = importProfiles([{label: 'personal', world: 'https://s1.sfgame.eu'},
                                {label: 'Research', world: 's1.sfgame.eu'}], first);
  assert.equal(second.length, 2);
  assert.equal(second[0].id, first[0].id);
  assert.notEqual(second[0].id, second[1].id);
  assert.deepEqual(validateSaved({schema: 1, accounts: second}), second);
});

test('rejects credentials, foreign origins, paths and malformed saved profiles', () => {
  for (const url of ['http://sfgame.net', 'https://sfgame.net.evil.invalid',
                     'https://user:pass@sfgame.net', 'https://sfgame.net:444',
                     'https://sfgame.net/login?token=x', 'javascript:alert(1)']) {
    assert.throws(() => gameOrigin(url));
  }
  assert.throws(() => importProfiles([{label: 'One', password: 'not-a-real-password'}]));
  const rows = importProfiles([{label: 'One'}]);
  assert.throws(() => validateSaved({schema: 1, accounts: [rows[0], rows[0]]}));
  assert.throws(() => importProfiles([{label: 'Good'}, {label: ''}]));
  assert.equal(gameOrigin('sfgame.net'), 'https://play.sfgame.net');
  assert.equal(gameOrigin(), 'https://play.sfgame.net');
});

test('embedded pages cannot navigate to arbitrary remote or local origins', () => {
  assert.ok(navigationAllowed('https://sso.playa-games.com/json/login'));
  assert.ok(navigationAllowed('https://s1.sfgame.eu/'));
  assert.ok(navigationAllowed('http://127.0.0.1:1234/', 'http://127.0.0.1:1234'));
  for (const url of ['file:///C:/Windows', 'http://127.0.0.1:1235',
                     'https://sfgame.net.evil.invalid', 'https://user@sfgame.net']) {
    assert.equal(navigationAllowed(url, 'http://127.0.0.1:1234'), false);
  }
});
