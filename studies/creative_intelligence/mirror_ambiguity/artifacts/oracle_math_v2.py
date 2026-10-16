"""Separate bounded verification oracle; imports no diagnostic implementation.

Compare distances directly using certified rational enclosures of square roots.
Rational roots are handled exactly so equality tests need no rounding tolerance.
"""
from fractions import Fraction as F
from itertools import permutations
from math import isqrt


def root_exact(q):
    q = F(q)
    a, b = isqrt(q.numerator), isqrt(q.denominator)
    return F(a, b) if a*a == q.numerator and b*b == q.denominator else None


def root_interval(q, bits):
    q = F(q)
    exact = root_exact(q)
    if exact is not None:
        return exact, exact
    scale = 1 << bits
    low = isqrt(q.numerator * scale * scale // q.denominator)
    return F(low, scale), F(low + 1, scale)


def fixed_oracle(q, y, epsilon):
    q, y, epsilon = map(F, (q, y, epsilon))
    assert q >= 0 and y >= 0 and epsilon >= 0
    exact = root_exact(q)
    if exact is not None:
        return abs(exact-y) <= epsilon
    for bits in (16, 32, 64, 128, 256, 512, 1024):
        lo, hi = root_interval(q, bits)
        if lo >= y-epsilon and hi <= y+epsilon:
            return True
        if hi < y-epsilon or lo > y+epsilon:
            return False
    raise AssertionError("fixed oracle budget exhausted")


def common_oracle(q, r, epsilon):
    q, r, epsilon = map(F, (q, r, epsilon))
    assert q >= 0 and r >= 0 and epsilon >= 0
    if q == r:
        return True
    a, b = root_exact(q), root_exact(r)
    if a is not None and b is not None:
        return abs(a-b) <= 2*epsilon
    # For unequal q,r and rational epsilon, equality with a nonzero rational
    # difference implies both roots rational. Thus remaining cases separate.
    for bits in (16, 32, 64, 128, 256, 512, 1024):
        lq, uq = root_interval(q, bits)
        lr, ur = root_interval(r, bits)
        # Intersect observation intervals after clipping at zero.
        upper_for_lower = max(F(0), uq-epsilon, ur-epsilon)
        lower_for_upper = min(lq+epsilon, lr+epsilon)
        if upper_for_lower <= lower_for_upper:
            return True
        lower_for_lower = max(F(0), lq-epsilon, lr-epsilon)
        upper_for_upper = min(uq+epsilon, ur+epsilon)
        if lower_for_lower > upper_for_upper:
            return False
    raise AssertionError("common oracle budget exhausted")


def distance_squared(anchor, time, trajectory):
    a, t, c = list(map(F, anchor)), F(time), list(map(F, trajectory))
    return (c[0]+t*c[2]-a[0])**2 + (c[1]+t*c[3]-a[1])**2


def candidate_oracle(data):
    candidates = data["candidates"]
    rows = [[distance_squared(a, t, c) for a, t in zip(data["anchors"], data["times"])]
            for c in candidates]
    witnesses = []
    for j in range(len(candidates)):
        for k in range(j+1, len(candidates)):
            if all(F(candidates[j][i]) == F(candidates[k][i]) for i in (0, 1)):
                continue
            if data.get("mode", "fixed_y") == "fixed_y":
                ok = all(fixed_oracle(q, y, e) for row in (rows[j], rows[k])
                         for q, y, e in zip(row, data["y"], data["epsilon"]))
            else:
                ok = all(common_oracle(q, r, e) for q, r, e in
                         zip(rows[j], rows[k], data["epsilon"]))
            if ok:
                witnesses.append([j, k])
    return witnesses, rows


def determinant_expansion(matrix):
    """Permutation sum, deliberately different from v1 Gaussian elimination."""
    total = F(0)
    for perm in permutations(range(len(matrix))):
        inv = sum(perm[i] > perm[j] for i in range(len(perm)) for j in range(i+1, len(perm)))
        product = F((-1)**inv)
        for i, j in enumerate(perm):
            product *= matrix[i][j]
        total += product
    return total
