import pytest
from statsx import mean, median, percentile, stddev


# mean
def test_mean_happy():
    assert mean([1, 2, 3, 4, 5]) == 3.0

def test_mean_single():
    assert mean([7]) == 7.0

def test_mean_empty():
    with pytest.raises(ValueError):
        mean([])


# median
def test_median_odd():
    assert median([3, 1, 2]) == 2

def test_median_even():
    assert median([1, 2, 3, 4]) == 2.5

def test_median_single():
    assert median([42]) == 42

def test_median_empty():
    with pytest.raises(ValueError):
        median([])


# percentile
def test_percentile_p0():
    assert percentile([10, 20, 30], 0) == 10

def test_percentile_p100():
    assert percentile([10, 20, 30], 100) == 30

def test_percentile_p50():
    assert percentile([1, 2, 3, 4, 5], 50) == 3.0

def test_percentile_single():
    assert percentile([99], 75) == 99

def test_percentile_empty():
    with pytest.raises(ValueError):
        percentile([], 50)

def test_percentile_p_below_range():
    with pytest.raises(ValueError):
        percentile([1, 2, 3], -1)

def test_percentile_p_above_range():
    with pytest.raises(ValueError):
        percentile([1, 2, 3], 101)


# stddev
def test_stddev_happy():
    result = stddev([2, 4, 4, 4, 5, 5, 7, 9])
    assert abs(result - 2.0) < 1e-9

def test_stddev_two_elements():
    result = stddev([0, 2])
    assert abs(result - 2**0.5) < 1e-9

def test_stddev_single_raises():
    with pytest.raises(ValueError):
        stddev([5])

def test_stddev_empty_raises():
    with pytest.raises(ValueError):
        stddev([])
