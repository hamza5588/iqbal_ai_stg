from app.services.quiz.math_text import recover_latex, recover_fields
from app.services.lms.mcq_utils import harvest_native_mcqs
assert 'x^{2}' in recover_latex('4x2 - 7x')
assert '\\frac' in recover_latex('Simplify:\n(a3b2)(a2b4)\nab3')
assert recover_latex('16 2 3 %') == '16 2/3%'
assert recover_latex('16\\frac{2}{3}%') == '16 2/3%'
assert '\\(' not in recover_latex('\\frac{\\((a^{3}b^{2})\\)\\((a^{2}b^{4})\\)}{ab^{3}}')
stem, _ = recover_fields('Which is the correct factorization of x2 + x - 12?', None)
assert 'Which is the correct' in stem and 'x^{2}' in stem
from app.services.quiz.math_text import unsquash_english, looks_like_prose
assert unsquash_english('Whichisthecorrectfactorizationofx') == 'Which is the correct factorization of x'
assert looks_like_prose('Whichisthecorrectfactorizationofx^2+x-12?')
smashed, _ = recover_fields('Whichisthecorrectfactorizationofx^2+x-12?', None)
assert 'Which is the correct factorization of' in smashed
assert 'Whichisthe' not in smashed
assert 'x^{2}' in smashed or 'x^2' in smashed
degree, _ = recover_fields('Whatisthedegreeofthepolynomial4x^{3}y^{2} - 7xy^{4} + 3?', None)
assert 'What is the degree of the polynomial' in degree
assert 'Whatisthe' not in degree
assert 'polynomial 4x' in degree
assert 'polynomial^{4}' not in degree
frac = recover_latex('Simplify:\n\\frac{\\((a^{3}b^{2})\\)\\((a^{2}b^{4})\\)}{ab^{3}}')
assert '\\frac' in frac and '\\(' not in frac
wrapped, _ = recover_fields('\\(Whichisthecorrectfactorizationofx^2+x-12?\\)', None)
assert wrapped.startswith('Which is the')
pdf = (
    '1. The number 5.181818... is\nA. a terminating decimal\n'
    'B. a repeating decimal\nC. a non-repeating decimal\nD. an irrational number\n'
    'Answer: B\n2. Choose the correct statement.\nA. one\nB. two\nC. three\nD. four\n'
    'Ans. D\n3. What is log 2 + log 5?\nA. 1\nB. log 7\nC. 0\nD. 2\n'
    'The correct answer is 1\n4. Solve x + 1 = 0\nA. -1\nB. 0\nC. 1\nD. 2\n'
    'Solution: -1\n'
)
mcqs = harvest_native_mcqs(pdf)
assert [m.get('answer') for m in mcqs] == ['B', 'D', '1', '-1'], mcqs
print('parser_ok', len(mcqs), 'math_ok')
