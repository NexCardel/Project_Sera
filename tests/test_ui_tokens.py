import re
from pathlib import Path

from ui.utils import theme
from ui.utils.tokens import TOKENS

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


def test_stylesheet_resolves_tokens():
    assert "$" not in theme.LIGHT_STYLESHEET
    assert TOKENS["em"] in theme.LIGHT_STYLESHEET
    assert f"font-size: {TOKENS['f_m']}px;" in theme.LIGHT_STYLESHEET
