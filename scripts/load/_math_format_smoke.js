const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = path.resolve(__dirname, '..', '..');
const code = fs.readFileSync(path.join(root, 'static', 'lms', 'lms-core.js'), 'utf8');
const window = {};
const sandbox = { window, console };
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

console.log('js_math_ok');
