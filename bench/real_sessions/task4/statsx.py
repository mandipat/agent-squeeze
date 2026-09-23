"""Tiny stats helpers for the benchmark session."""
import math

def mean(xs):
    if not xs:
        raise ValueError("mean of empty sequence")
    return sum(xs) / len(xs)

def median(xs):
    if not xs:
        raise ValueError("median of empty sequence")
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return (s[mid - 1] + s[mid]) / 2 if n % 2 == 0 else s[mid]

def percentile(xs, p):
    if not xs:
        raise ValueError("percentile of empty sequence")
    if not 0 <= p <= 100:
        raise ValueError("p must be in [0, 100]")
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100
    f = math.floor(k)
    c = math.ceil(k)
    return s[f] + (s[c] - s[f]) * (k - f)

def stddev(xs):
    if len(xs) < 2:
        raise ValueError("stddev needs at least 2 values")
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
