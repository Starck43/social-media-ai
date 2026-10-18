const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const scheduleSource = fs.readFileSync('app/static/js/task-schedule.js', 'utf8');
const context = { window: {} };
vm.runInNewContext(scheduleSource, context, { filename: 'task-schedule.js' });
const build = context.window.taskScheduleBuilder;

const cronCases = [
  '5 9 * 1,3 *',
  '*/15 * * * *',
  '0 9 * * 0',
  '5 9 31 * *',
  '15 */3 * * *',
];
for (const cron of cronCases) {
  assert.equal(build(cron).cron, cron, `preserves ${cron} until an explicit edit`);
}

assert.equal(build('*/15 * * * *').freq, 'custom');
assert.deepEqual([...build('0 9 * * 0').weekdays], [7]);
assert.equal(build('5 9 31 * *').monthday, 31);
assert.equal(build('15 */3 * * *').freq, 'custom');

const custom = build('*/15 * * * *');
custom.rawCron = '*/20 * * * *';
custom.markChanged();
assert.equal(custom.cron, '*/20 * * * *');

const builder = build('*/15 * * * *');
builder.useBuilder();
assert.equal(builder.cron, '00 09 * * *');
builder.applyPreset('weekly');
assert.equal(builder.cron, '00 09 * * 1');
builder.applyPreset('every_6_hours');
assert.equal(builder.cron, '0 */6 * * *');
builder.applyPreset('once');
assert.equal(builder.cron, '@once');

builder.load('5 9 31 * *');
assert.equal(builder.cron, '5 9 31 * *');
builder.markChanged();
assert.notEqual(builder.cron, '5 9 31 * *');

console.log(`task schedule checks passed (${cronCases.length} cron preservation cases)`);
