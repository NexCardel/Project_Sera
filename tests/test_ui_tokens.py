import math
import re
from pathlib import Path

from ui.utils import theme
from ui.utils.tokens import KIT, TOKENS

ROOT = Path(__file__).resolve().parents[1]
MOCKUP = ROOT / "Mockups" / "fleet" / "index.html"
README = ROOT / "Mockups" / "fleet" / "README.md"


def _mockup_root_tokens():
    html = MOCKUP.read_text(encoding="utf-8")
    block = re.search(r":root\{(.*?)\}", html, re.S).group(1)
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"--([a-z0-9-]+)\s*:\s*([^;]+);", block)}


def _readme_token_names():
    text = README.read_text(encoding="utf-8")
    section = text.split("## Tokens", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"`--([a-z0-9-]+)`", section))


def _key(css_name):
    return css_name.replace("-", "_")


def test_every_readme_token_exists():
    missing = sorted(n for n in _readme_token_names() if _key(n) not in TOKENS)
    assert not missing, f"tokens.py lacks: {missing}"


def test_tokens_match_mockup_root_block():
    mockup = _mockup_root_tokens()
    assert {_key(n) for n in mockup} == set(TOKENS)
    for css_name, value in mockup.items():
        ours = TOKENS[_key(css_name)]
        if isinstance(ours, int):
            ours = f"{ours}px"
        assert str(ours).lower() == value.lower(), css_name


def _css_rule(selector):
    html = MOCKUP.read_text(encoding="utf-8")
    m = re.search(r"(?:^|[}\s])" + re.escape(selector) + r"\{([^}]*)\}", html)
    assert m, selector
    return m.group(1)


def _css_px(selector, prop, index=0):
    body = _css_rule(selector)
    value = re.search(r"(?:^|;)" + re.escape(prop) + r":([^;]+)", body).group(1)
    return float(re.findall(r"(\d+(?:\.\d+)?)px", value)[index])


# (KIT key, selector, property, index of the px value in it); half pixels round up as in K-1.
_POLISH_SIZES = [
    ("d_icon", "svg.i", "width", 0),
    ("d_icon_s", "svg.i.s", "width", 0),
    ("d_icon_l", "svg.i.l", "width", 0),
    ("d_ring_icon", "svg.i.l", "width", 0),
    ("pad_main_top", ".main", "padding", 0),
    ("pad_main_h", ".main", "padding", 1),
    ("pad_main_bot", ".main", "padding", 2),
    ("gap_main", ".main", "gap", 0),
    ("pad_main_top_c", ".app.compact .main", "padding", 0),
    ("pad_main_h_c", ".app.compact .main", "padding", 1),
    ("pad_main_bot_c", ".app.compact .main", "padding", 2),
    ("pad_count_h", ".count", "padding", 1),
    ("r_count", ".count", "border-radius", 0),
    ("f_chip", ".chip", "font", 0),
    ("f_btn", ".btn", "font", 0),
    ("gap_chip", ".chip", "gap", 0),
    ("d_svc_ic", ".svc .ic", "width", 0),
    ("r_svc_ic", ".svc .ic", "border-radius", 0),
    ("d_ibtn_kv", ".kv .v .ibtn", "width", 0),
    ("f_sub", ".idchip", "font", 0),
    ("pad_idchip_v", ".idchip", "padding", 0),
    ("pad_idchip_h", ".idchip", "padding", 1),
    ("f_tile_num", ".tile b", "font-size", 0),
    ("w_stack", ".stack", "max-width", 0),
    ("pad_set_top", ".set-main", "padding", 0),
    ("pad_set_h", ".set-main", "padding", 1),
    ("pad_set_bot", ".set-main", "padding", 2),
    ("f_set_title", ".set-main h2", "font-size", 0),
    ("w_mc_list", ".mc", "grid-template-columns", 0),
    ("w_mc_list_c", ".app.compact .mc", "grid-template-columns", 0),
    ("gap_mc", ".mc", "gap", 0),
    ("d_avatar_ed", ".mc .ed-hd .av", "width", 0),
    ("r_avatar_ed", ".mc .ed-hd .av", "border-radius", 0),
    ("f_avatar_ed", ".mc .ed-hd .av", "font-size", 0),
]


def test_polish_kit_sizes_equal_the_mockup():
    for key, selector, prop, index in _POLISH_SIZES:
        assert KIT[key] == math.ceil(_css_px(selector, prop, index)), (key, selector)


def test_polish_kit_derived_sizes():
    # svg.i fills its box; qtawesome paints the glyph at 0.875 of it
    assert abs(KIT["icon_scale"] * 0.875 - 1.0) < 0.01
    # .count: 12 px x 1.4 line + 1 + 1 padding + 2 border; .tile b 20 x 1.1; tile label 12 x 1.4
    assert KIT["h_count"] == round(TOKENS["f_s"] * 1.4) + 2 + 2
    assert KIT["h_tile_num"] == round(KIT["f_tile_num"] * 1.1)
    assert KIT["h_tile_num_sm"] == round(KIT["f_tile_num_sm"] * 1.1)
    assert KIT["h_tile_lbl"] == round(TOKENS["f_s"] * 1.4)
    assert KIT["gap_btn_icon"] == 7 - 4  # .btn gap 7 minus Qt's own icon-text spacing
    assert KIT["w_pager_sel"] == 72  # style="width:72px" on the tracker's Rows per page
    for key in ("svc_ic_bg", "kv_ibtn_tx", "notset_tx", "notset_b"):
        assert re.fullmatch(r"#[0-9A-F]{6}", KIT[key]), key


def test_stylesheet_resolves_tokens():
    assert "$" not in theme.LIGHT_STYLESHEET
    assert TOKENS["em"] in theme.LIGHT_STYLESHEET
    assert f"font-size: {TOKENS['f_m']}px;" in theme.LIGHT_STYLESHEET
