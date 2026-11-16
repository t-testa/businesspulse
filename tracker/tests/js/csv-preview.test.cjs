const test = require('node:test');
const assert = require('node:assert/strict');
const {parsePreview, suggestMapping} = require('../../static/tracker/csv-upload.js');

test('preview includes every header and at most ten data rows', () => {
  const result = parsePreview('A,B\n' + Array.from({length: 15}, (_, i) => `${i},value`).join('\n'));
  assert.deepEqual(result.headers, ['A', 'B']);
  assert.equal(result.rows.length, 10);
  assert.equal(result.rows[9][0], '9');
});
test('UTF-8 BOM, CRLF, commas, escaped quotes, quoted newlines and trailing empty cells', () => {
  assert.deepEqual(parsePreview('\uFEFFA,B,C\r\n"hello, world","line1\r\nline2 ""quoted""",\r\n').rows,
    [['hello, world', 'line1\r\nline2 "quoted"', '']]);
});
test('ambiguous aliases stay unselected', () => {
  assert.deepEqual(suggestMapping(['Order ID', 'Revenue', 'Amount', 'COGS'], {
    transaction_id: ['transaction_id', 'order_id'], revenue: ['revenue', 'amount'], cost: ['cost', 'cogs'],
  }), {transaction_id: 'Order ID', revenue: '', cost: 'COGS'});
});
test('invalid header, field count and quoting produce actionable errors', () => {
  for (const text of ['', 'A,B\n', 'A,a\nx,y', 'A,\nx,y', 'A,B\nx', 'A,B\nx,y,z', 'A,B\n"unterminated,x', 'A,B\n"x"oops,y', 'A,B\nx,\0']) {
    assert.throws(() => parsePreview(text));
  }
  assert.throws(() => parsePreview('A,B\nx,y', 1));
  assert.throws(() => parsePreview('LONG\nx', 100, 3));
});
test('HTML and formula cells remain inert strings', () => {
  assert.deepEqual(parsePreview('A,B\n<script>alert(1)</script>,=1+1').rows,
    [['<script>alert(1)</script>', '=1+1']]);
});
test('reordered source names map by name and missing aliases remain blank', () => {
  assert.deepEqual(suggestMapping(['Cost', 'Receipt'], {transaction_id: ['id'], cost: ['cost']}),
    {transaction_id: '', cost: 'Cost'});
});
