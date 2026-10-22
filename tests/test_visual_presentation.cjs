const assert = require('node:assert/strict');
const {test} = require('node:test');
const {beats,reward,heading,goldValue,recoveryActions,effectArrival} = require('../web/presentation.js');

test('damage and healing remain separate even if net health is unchanged', () => {
 const outcomes=[{kind:'damage',amount:.2},{kind:'healing',amount:.2}];
 const before=JSON.stringify(outcomes),result=beats({hp:.7},{hp:.7},outcomes);
 assert.equal(result.length,2);
 assert.ok(Math.abs(result[0].hp-.5)<1e-9);
 assert.ok(Math.abs(result[1].hp-.7)<1e-9);
 assert.equal(JSON.stringify(outcomes),before);
});
test('empty rewards never invent damage and ordinary transitions stay quiet', () => {
 assert.deepEqual(beats({hp:.8},{hp:.8},[]),[]);
 assert.deepEqual(beats({hp:.8},{hp:.8},[{kind:'nothing',amount:0}]),[{kind:'nothing',amount:0,hp:.8}]);
 assert.equal(reward({kind:'nothing',amount:0}).kind,'empty');
});
test('item rarities, joint rewards and blessings retain their identity', () => {
 const cards=[{kind:'gold',amount:1},{kind:'item',amount:0},{kind:'epics',amount:1},{kind:'legendaries',amount:1},{kind:'blessing',detail:'one_hit',duration:8}].map(e=>reward(e));
 assert.deepEqual(cards.map(c=>c.kind),['gold','item','epic','legendary','blessing']);
 assert.match(cards[4].note,/8 uses/);
 assert.equal(cards[4].asset,'effect.one_hit');
 assert.deepEqual(cards.slice(1,4).map(c=>c.asset),['reward.item','reward.epic','reward.legendary']);
 assert.equal(goldValue(1.5),'15,000,000');
 assert.equal(heading([{kind:'awakened'}],{encounter:'skeleton'},{}),'The soldier wakes up!');
});
test('boss recovery shows useful targets, including the precise recommendation, without overshooting the hit ceiling',()=>{
 const wait=hp=>({kind:'wait',index:0,value:hp*24});
 const o={phase:'recovery',room:100,hp:0,hours_left:168,boss_damage_max:.525,scenario:{recovery_hours:24},legal_actions:[wait(.2),wait(.4),wait(.6),wait(.8),wait(1),wait(.526),{kind:'wait',value:168}]};
 assert.deepEqual(recoveryActions(o,wait(.52)).map(a=>Math.round(a.value/24*1000)),[200,400,526,520]);
 const max=o.legal_actions.at(-1);assert.ok(!recoveryActions({...o,room:99},wait(.21)).includes(max));
 const trained={...o,neural_adviser:{case:'unrestricted_budget_policy'}};
 assert.ok(recoveryActions(trained,wait(.8)).some(a=>a.value===wait(.8).value),'The actual trained wait must remain visible');
});

test('incoming effects replace their own slot even when weaker, independently of curses',()=>{
 const state={blessings:[{kind:'recovery',remaining:6},{kind:'one_hit',remaining:8},{kind:'escape',remaining:10}],
  curses:[{kind:'poison'},{kind:'clumsy'},{kind:'hard_lock'}]},snapshot=JSON.stringify(state);
 const plan=effectArrival(state,{kind:'blessing',detail:'one_hit',duration:4,amount:1},{});
 assert.equal(plan.group,'blessings');assert.equal(plan.slot,1);
 assert.deepEqual(plan.effects.blessings.map(e=>[e.kind,e.remaining]),[['recovery',6],['one_hit',4],['escape',10]]);
 assert.deepEqual(plan.effects.curses,state.curses);assert.equal(JSON.stringify(state),snapshot);
});

test('fourth distinct curse flies to the newest slot and evicts only the oldest curse',()=>{
 const state={blessings:[{kind:'one_hit'}],curses:[{kind:'poison'},{kind:'clumsy'},{kind:'hard_lock'}]};
 const plan=effectArrival(state,{kind:'curse',detail:'broken_armor',duration:5,amount:.25},{});
 assert.equal(plan.group,'curses');assert.equal(plan.slot,2);
 assert.deepEqual(plan.effects.curses.map(e=>e.kind),['clumsy','hard_lock','broken_armor']);
 assert.deepEqual(plan.effects.blessings,state.blessings);
});

test('an instant elixir targets health and never displaces an active blessing',()=>{
 const state={blessings:[{kind:'one_hit'},{kind:'recovery'},{kind:'escape'}],curses:[]};
 const plan=effectArrival(state,{kind:'blessing',detail:'elixir',amount:.5,duration:0},{});
 assert.equal(plan.group,null);assert.equal(plan.slot,null);assert.deepEqual(plan.effects,state);
 assert.equal(reward({kind:'gold',amount:1}).symbol,'🪙');
});
