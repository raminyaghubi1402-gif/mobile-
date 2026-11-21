const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('docs/chart.html', 'utf8');
const match = html.match(/function assessCandleFreshness\([\s\S]*?\n\}/);
assert.ok(match, 'freshness helper must exist in chart');
const context = {};
vm.runInNewContext(match[0] + '\nthis.assess=assessCandleFreshness;', context);
const assess = context.assess;
const bars = times => times.map(time => ({time,open:1,high:2,low:0.5,close:1.5}));

assert.equal(assess(bars([600,900]),'5',1200).fresh, true, 'just-closed bar is fresh');
assert.equal(assess(bars([600,900]),'5',1800).fresh, false, 'stale 5m data fails closed');
assert.equal(assess(bars([600,900]),'5',1200).ageSeconds, 0);
assert.equal(assess([], '5', 1200).fresh, false, 'empty data is not fresh');
assert.equal(assess(bars([900,600]),'5',1200).fresh, false, 'nonchronological data fails');
assert.equal(assess(bars([900]),'5',1100).fresh, false, 'future/unclosed candle fails');
assert.equal(assess(bars([600,1500]),'5',1800).fresh, false, 'recent interval gap fails');
assert.equal(assess(bars([0]),'D',86400).fresh, true, 'closed daily candle within tolerance is fresh');
assert.equal(assess(bars([0]),'D',259200).fresh, false, 'old daily candle fails');

const symbolSelect = html.match(/<select id="symbol">([\s\S]*?)<\/select>/);
assert.ok(symbolSelect, 'market selector exists');
const values = [...symbolSelect[1].matchAll(/<option value="([^"]+)"/g)].map(x=>x[1]);
assert.deepEqual(values, ['BTCIRT','ETHIRT'], 'chart market scope must stay restricted');
assert.ok(!html.includes('setInterval('), 'chart must not poll continuously');
const refresh = html.slice(html.indexOf('async function refreshMtfAnalysis'), html.indexOf('\nfunction breakoutRetestConfirmed'));
assert.ok(refresh.includes('assessCandleFreshness(closed,key'), 'primary timeframe freshness is checked');
assert.ok(refresh.indexOf('assessCandleFreshness(closed,key') < refresh.indexOf('mtfReady=true'), 'freshness guard precedes ready flag');
assert.ok(refresh.includes('throw new Error'), 'stale primary data must stop the analysis');
console.log('Chart data guard tests: PASS');
