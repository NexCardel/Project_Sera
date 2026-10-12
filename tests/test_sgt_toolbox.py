

def test_collapse_repeat_collapses_a_name_printed_twice_but_not_a_one_word_repeat():
    from core.sgt.sgt_toolbox import TRANSFORMS
    f = TRANSFORMS["collapse_repeat"]
    assert f("TULIP FASHION TULIP FASHION") == "TULIP FASHION"
    assert f("Tulip Fashion TULIP FASHION") == "Tulip Fashion"
    assert f("RAM KUMAR SEN RAM KUMAR SEN") == "RAM KUMAR SEN"
    assert f("SINGH SINGH") == "SINGH SINGH"
    assert f("TULIP FASHION") == "TULIP FASHION"
    assert f("A B A C") == "A B A C"
