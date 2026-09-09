"""Retake question-set selection: attempt 1 = originals in order; a retake
is shuffled and swaps in a variant per slot where the pool has one."""
import importlib.util
import pathlib
import sys
import types

_p = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "lms" / "diagnostic_variant_service.py"


def _load():
    # Stub the three app modules diagnostic_variant_service imports so it can
    # load without sqlalchemy / a DB. Only select_and_shuffle is exercised.
    for name in ("app", "app.models", "app.models.lms_models", "app.services",
                 "app.services.lms", "app.services.lms.assessment_service", "app.utils", "app.utils.db"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["app.models.lms_models"].Assessment = object
    sys.modules["app.services.lms.assessment_service"].get_assessment = lambda *_a, **_k: None
    sys.modules["app.utils.db"].get_db = lambda: None
    spec = importlib.util.spec_from_file_location("_dvs_under_test", _p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.select_and_shuffle


select_and_shuffle = _load()

ORIGINALS = [10, 11, 12, 13, 14]
POOL = {"10": [110, 210], "12": [112], "13": [113, 213, 313]}


def test_first_attempt_is_originals_in_order():
    assert select_and_shuffle(ORIGINALS, POOL, "s:1", 1) == ORIGINALS


def test_retake_is_a_permutation_with_variants_swapped_in():
    out = select_and_shuffle(ORIGINALS, POOL, "44:2", 2)
    assert sorted(out) == sorted([110, 11, 112, 113, 14])  # slot 10->110, 12->112, 13->113
    assert out != [110, 11, 112, 113, 14]  # shuffled


def test_retake_is_deterministic_for_a_given_seed():
    a = select_and_shuffle(ORIGINALS, POOL, "44:2", 2)
    b = select_and_shuffle(ORIGINALS, POOL, "44:2", 2)
    assert a == b


def test_third_attempt_picks_the_next_variant():
    out3 = select_and_shuffle(ORIGINALS, POOL, "44:3", 3)
    # slot 10 has [110,210] -> attempt 3 picks index (3-2)%2 = 1 -> 210
    assert 210 in out3 and 110 not in out3
    # slot 12 has one variant -> always 112
    assert 112 in out3


def test_slot_without_variants_keeps_original():
    out = select_and_shuffle(ORIGINALS, POOL, "44:2", 2)
    assert 11 in out and 14 in out  # 11 and 14 have no pool entry
