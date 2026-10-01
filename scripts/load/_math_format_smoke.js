const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = path.resolve(__dirname, '..', '..');
const code = fs.readFileSync(path.join(root, 'static', 'lms', 'lms-core.js'), 'utf8');
const window = {};
const document = {
  addEventListener: function () {},
  readyState: 'complete',
  querySelectorAll: function () { return []; },
};
const sandbox = { window, document, console };
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

const prepare = window.lmsPrepareMathText;
const pick = window.lmsPickDisplayText;

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const q11 = prepare('Whichisthecorrectfactorizationofx^2+x-12?');
console.log('Q11:', q11);
assert(q11.indexOf('Which is the correct factorization of') === 0, 'Q11 spaces: ' + q11);
assert(q11.indexOf('Whichisthe') < 0, 'Q11 still smashed');
assert(q11.indexOf('\\(') !== 0, 'Q11 whole-stem wrap');

const q11w = prepare('\\(Whichisthecorrectfactorizationofx^2+x-12?\\)');
console.log('Q11 wrapped:', q11w);
assert(q11w.indexOf('Which is the correct') === 0, 'Q11 unwrap: ' + q11w);

const q14 = prepare('Whatisthedegreeofthepolynomial4x^{3}y^{2} - 7xy^{4} + 3?');
console.log('Q14:', q14);
assert(q14.indexOf('What is the degree of the polynomial') === 0, 'Q14 spaces: ' + q14);
assert(q14.indexOf('polynomial^{4}') < 0, 'Q14 glued exponent');
assert(q14.indexOf('\\(') !== 0, 'Q14 whole-stem wrap');
assert(/\\?\(.*4x\^\{3\}/.test(q14) || q14.indexOf('4x^{3}') >= 0, 'Q14 polynomial kept');

const q13 = prepare('Simplify:\n\\frac{\\((a^{3}b^{2})\\)\\((a^{2}b^{4})\\)}{ab^{3}}');
console.log('Q13:', q13);
assert(q13.indexOf('Simplify:') === 0, 'Q13 label');
assert(q13.indexOf('\\frac{(a^{3}b^{2})(a^{2}b^{4})}{ab^{3}}') >= 0, 'Q13 frac body: ' + q13);
assert(!/\\frac\{[^}]*\\\(/.test(q13), 'Q13 inner delims: ' + q13);

const q11p = prepare(pick(
  'Whichisthecorrectfactorizationofx^2+x-12?',
  'Whichisthecorrectfactorizationofx^2+x-12?'
));
console.log('Q11 pick:', q11p);
assert(q11p.indexOf('Which is the correct factorization of') === 0, 'Q11 pick+prep: ' + q11p);

const slash = prepare('Simplify and reduce 4y/(y^2 - 1) - (y + 1)/(y - 1) to its lowest form.');
console.log('slash:', slash);
assert(slash.indexOf('\\frac{4y}') >= 0, 'slash→frac num: ' + slash);
assert(slash.indexOf('4y/') < 0, 'slash leftover: ' + slash);

const power = prepare('If a^x = y, where a > 0 and a ≠ 1, which is its logarithmic form?');
console.log('power:', power);
assert(/\\\(a\^x\s*=\s*y\\\)/.test(power) || /\\\(a\^x\\\)/.test(power), 'a^x wrap: ' + power);

const loga = prepare('Find the value of a if log_a 8 = 3/2 where a > 0 and a ≠ 1.');
console.log('loga:', loga);
assert(/\\\(\\log_\{a\}/.test(loga) || loga.indexOf('\\(\\log_{a}') >= 0, 'log wrap: ' + loga);
assert(loga.indexOf('\\frac{3}{2}') >= 0, '3/2 frac: ' + loga);

// Server-style already-wrapped render must NOT grow extra backslashes.
const rendered = prepare('Find the value of a if \\(\\log_{a} 8 = \\frac{3}{2}\\) where a > 0 and a ≠ 1.');
console.log('rendered:', rendered);
assert(rendered.indexOf('\\\\\\(') < 0, 'no triple backslash: ' + rendered);
assert(rendered.indexOf('\\\\log') < 0, 'no doubled log: ' + rendered);
assert(rendered.indexOf('\\(\\log_{a}') >= 0, 'keeps single wrap: ' + rendered);

const times = prepare('find log (2 \\times 5)');
console.log('times:', times);
assert(times.indexOf('\\times') >= 0 && times.indexOf('\\(') >= 0, 'times wrap: ' + times);

const q18 = prepare('rewrite as a single logarithm: \\(\\log_{5}(b^{2})\\) \\times \\(\\log_{a}(5^{3})\\)');
console.log('q18:', q18);
assert(q18.indexOf('\\times') >= 0, 'q18 keeps times: ' + q18);
assert(!/\\\)\s*\\times\s*\\\(/.test(q18), 'q18 joins times spans: ' + q18);

const broken = prepare('Find the value of a if \\\\\\(\\log_{a} 8 = \\\\frac{3}{2}\\\\\\) where a > 0.');
console.log('broken:', broken);
assert(broken.indexOf('\\\\\\(') < 0, 'repairs triple: ' + broken);
assert(broken.indexOf('\\(\\log_{a}') >= 0 || broken.indexOf('\\log_{a}') >= 0, 'log ok: ' + broken);

console.log('js_math_ok');
