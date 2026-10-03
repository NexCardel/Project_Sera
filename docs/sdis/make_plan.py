"""Writes sdis-plan.json (run once by hand when re-planning; the dispatcher only reads the JSON).

    ../APP/venv/Scripts/python.exe docs/sdis/make_plan.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUTOFILL = HERE.parent / "autofill_tweaks" / "autofill-tweaks-plan.json"

PY = "../APP/venv/Scripts/python.exe"
T = f"{PY} -m pytest -q -p no:cacheprovider"
SDIS_TESTS = "tests/test_sdis_memory.py tests/test_class_diff_align.py"
REG = f"{PY} tools/sdis_regress.py"
COMMON = (f" EVERY WP: run `{REG}` before you change anything and again at the end; put both summaries"
          " (counts only) in your hand-off note and explain every number that changed. Run the SDIS tests"
          f" (`{T} tests/test_sdis_*.py tests/test_class_diff_align.py`). Never copy, print or quote"
          " anything from ../APP/tools/pre_dev/class_diff/output/ (real client data) except counts.")


def wp(wp_id, phase, model, size, deps, what, focus, kind="auto"):
    return {"wp": wp_id, "phase": phase, "model": model, "kind": kind, "size": size, "deps": deps,
            "what": what, "focus": focus + ("" if kind == "review" else COMMON)}


WPS = [
    wp("W0-1", 0, "sonnet", "M", [], "Baseline: tests + the regression runner tools/sdis_regress.py",
       "Blueprint section 0, Part A.3, section 9. The pre-dev engine is in tools/pre_dev/class_diff/ (keys.py,"
       " align.py, link_map.py, memory.py, compare.py). STEP 1: run"
       f" `{T} {SDIS_TESTS}` and record the result (expect 14 passed). STEP 2: write tools/sdis_regress.py."
       " It must: (a) take `--captures DIR`, default = the folder ../APP/tools/pre_dev/class_diff/output"
       " resolved from the repo root (REPO.parent / 'APP' / 'tools/pre_dev/class_diff/output'); if that does"
       " not exist, print 'no captures' and exit 0; (b) BEFORE importing link_map, set"
       " os.environ['SDIS_DATA_DIR'] to the captures folder and set link_map.OUT_DIR to it (link_map reads"
       " its captures from OUT_DIR; the worktree's own output/ folder is empty and git-ignored);"
       " (c) print, per (session, page link) map from link_map.build_maps(): snapshots merged, text nodes,"
       " DOUBLE COUNT (text nodes whose (align.shape(e), text) occurs more often in the merged map than in"
       " the snapshot that showed it most), LOST (texts some snapshot showed that are in no entry's"
       " 'values'), MULTI (entries holding more than one value); (d) per page link with 2+ clients"
       " (memory.client_maps()), build memory forward and in reversed client order (memory.build) and print"
       " each client's matched % and the verdict counts of memory.summary(), and 'orders agree: yes/no';"
       " (e) print total seconds. NEVER print a text or value, only counts, page links and session names."
       " Add `--save PATH` to also write the same printout to a file. STEP 3: run it and save it as"
       " docs/sdis/sdis-regress-baseline.txt (counts only, safe to commit). Expected today: double count 0"
       " and lost 0 on every map; services dashboard confirmed same/differs/composite 115/39/11; returns"
       " dashboard 90/6/7 plus 5 waiting; GSTR-1 92/13/18 plus 1 'changes within one client' and 4"
       " waiting; fictional GSTR-1 48/8 (+1 changes within one client) and GSTR-3B 55/8; orders agree yes"
       " everywhere. If your numbers differ, find out why before finishing and record it with `decide`."
       " STEP 4: tests/test_sdis_regress.py: copy tests/class_diff_align/client_A.json and client_B.json"
       " into tmp_path as key_probe_20260101_000000.json and key_probe_20260101_000100.json, run the"
       " runner's main(['--captures', str(tmp_path)]) and assert exit 0 and 'orders agree: yes' in the"
       " captured output (capsys)."),

    wp("W0-2", 0, "sonnet", "M", ["W0-1"], "Engine package core/sdis/ (pre-dev files become module aliases)",
       "Blueprint Part A.2 (the table and the alias rule are exact). STEP 1: create core/sdis/__init__.py"
       " (docstring: the pipeline capture -> merge (link_map) -> memory -> labels/relevance -> dialog, one"
       " line each) and core/sdis/paths.py with data_dir(): Path(os.environ['SDIS_DATA_DIR']) if set, else"
       " Path.home() / SERA_DATA_DIR_NAME / 'sdis' (import SERA_DATA_DIR_NAME from core.vsdc.vsdc_alerts,"
       " as core/sgt/sgt_corpus.py does), and write_csv() moved unchanged from tools/pre_dev/class_diff/"
       "compare.py. STEP 2: `git mv` tools/pre_dev/class_diff/keys.py, align.py, link_map.py, memory.py"
       " into core/sdis/. Change their imports to absolute core.sdis ones (from core.sdis.align import"
       " align, pair_moved, shape ...). keys.LEARNED_FILE becomes data_dir() / 'learned_state.json';"
       " link_map.OUT_DIR becomes data_dir(). memory.py: `from compare import ...` is not allowed in core;"
       " see step 3. STEP 3: create core/sdis/labels.py and MOVE into it from compare.py, unchanged:"
       " FIXABLE_TYPES, SENTENCE_WORDS, the period regexes (_MONTH, _YEAR_PREFIX, _PERIOD_RES), is_period,"
       " value_type, element_type, _end, composites, CT_HYPERLINK/CT_IMAGE/CT_TABLE, NEVER_LABEL_CTYPES."
       " Create core/sdis/identity.py and MOVE into it from compare.py: CLIENT_FIELDS, _registry,"
       " client_ids, masked. compare.py imports all of them back (`from core.sdis.labels import ...`) so"
       " compare.compare_flat and every name the tests use still exist on the compare module. memory.py"
       " imports composites from core.sdis.labels, client_ids from core.sdis.identity, write_csv from"
       " core.sdis.paths. STEP 4: recreate tools/pre_dev/class_diff/keys.py, align.py, link_map.py,"
       " memory.py as ALIASES, each exactly: a docstring naming the core module; `import os, sys`;"
       " `from pathlib import Path`; _HERE = Path(__file__).resolve().parent;"
       " os.environ.setdefault('SDIS_DATA_DIR', str(_HERE / 'output')); put _HERE.parents[2] on sys.path;"
       " `import core.sdis.<name> as _m`; then for link_map.py and memory.py (they have main()):"
       " `if __name__ == '__main__': raise SystemExit(_m.main())` `else: sys.modules[__name__] = _m`;"
       " for keys.py and align.py just `sys.modules[__name__] = _m`. WHY: tests and compare.py set"
       " keys.VIEW and compare.ALIGN on the module; a star-import copy would silently not reach"
       " flatten(). STEP 5: tests/test_sdis_package.py: with tools/pre_dev/class_diff on sys.path,"
       " `import keys` is core.sdis.keys; setting keys.VIEW = 'sgt' changes core.sdis.keys.VIEW; core.sdis"
       " imports without PySide6. STEP 6: switch tools/sdis_regress.py to import core.sdis directly (set"
       " SDIS_DATA_DIR first). The regression printout must be IDENTICAL to"
       " docs/sdis/sdis-regress-baseline.txt except the timing line; diff them and say so in the note."
       f" Prove the pre-dev scripts still import: `{PY} tools/pre_dev/class_diff/compare.py --help` and"
       f" `{PY} tools/pre_dev/class_diff/memory.py --help`."),

    wp("W1-1", 1, "sonnet", "S", ["W0-2"], "Part B: value history with times",
       "Blueprint Part B. In core/sdis/link_map.py LinkMap.add: each entry gets 'history', a list of"
       " [stamp, text]; append [stamp, text] when the element has text and it differs from the last"
       " history item's text (so a repeated text is stored once, a text that comes BACK after another is"
       " stored again). to_flat() adds 'history' to each item. In core/sdis/memory.py PageMemory._see: per"
       " client keep c['history'] (append the incoming items, skipping exact duplicates). Create"
       " core/sdis/history.py with day_of(stamp) -> 'YYYYMMDD': stamps look like '20261001_235545+0005.0'"
       " (capture snapshot), '20261001_235545' (single read) or an ISO time '2026-10-01T23:55:45' (the"
       " future recorder); take the first 8 digits after removing '-'; return '' if there are fewer."
       " Tests (tests/test_sdis_history.py): two snapshots with different texts -> 2 history items; the"
       " same text twice -> 1; A, B, A -> 3; day_of on all three formats; memory keeps each client's"
       " history apart. Nothing else changes: regression numbers identical."),

    wp("W1-2", 1, "sonnet", "L", ["W0-2"], "Part G: screens (one link, several pages) by weighted matching",
       "Blueprint Part G (formulas are exact). STEP 1: core/sdis/screens.py: link_weights(sources) ->"
       " {(shape, text): weight} where sources are link_map.all_sources() tuples; links = number of"
       " distinct page links in sources; weight = math.log((1 + links) / links_showing_it); a key not in"
       " the dict (new text) gets math.log(1 + links); nodes without text weigh 0. covers(flat_new,"
       " flat_old, pairs, weights) -> (cover_new, cover_old) where pairs is {index in new: index in old};"
       " cover = matched weight / total weight on that side; if a side's total weight < 1e-9 its cover is"
       " 1.0 (a loading shell or an empty map never forces a new screen). same_screen(cover_new, cover_old)"
       " = max(...) >= SCREEN_MIN (0.5, a module constant). STEP 2: core/sdis/link_map.py build_maps: compute"
       " weights once from the sources; for each read, the candidate maps are the owner's maps of that link"
       " (keys (owner, link) for screen 1, (owner, f'{link} [screen {n}]') for n >= 2). For each candidate:"
       " cur = m.to_flat(); pairs = pair_moved(flat, cur, align(flat, cur, _always), _always); covers."
       " Add the read to the candidate with the best max(cover) if same_screen, else to a NEW screen map."
       " Give LinkMap two attributes: link (the base link) and screen (1, 2, ...). Let LinkMap.add accept"
       " an optional precomputed (flat, pairs) so the alignment is not done twice. STEP 3:"
       " core/sdis/memory.py client_maps(): group by base link; for each link keep a list of PageMemory"
       " (one per screen, attribute screen); assign each client's screen map to the PageMemory whose"
       " current order view gives the best max(cover) (same weights) if same_screen, else start a new"
       " PageMemory. build()/main()/regress runner treat each PageMemory as its own page, printed as"
       " 'link [screen n]'. STEP 4: tests/test_sdis_screens.py with the fixture client_A.json (helpers"
       " like tests/test_sdis_memory.py's _subtrees/_rebuild): (a) A then A plus one extra top-level block"
       " -> one screen; (b) a one-node shell with no text, then A -> one screen; (c) A, then a page that"
       " keeps only A's first and last top-level blocks and replaces the rest with new blocks of new text"
       " -> two screens; (d) covers() maths on a tiny hand-made example. Regression: no link splits on the"
       " real captures and every other number is unchanged; if a real link splits, stop, investigate and"
       " explain."),

    wp("W1-3", 1, "sonnet", "L", ["W0-2"], "Part D: identity by session id + data fingerprint",
       "Blueprint Part D (thresholds exact). In the pre-dev captures a capture SESSION stands in for an"
       " SGT session. STEP 1: move group_clients and _by_time from core/sdis/link_map.py into"
       " core/sdis/identity.py (link_map re-exports them so old imports work). STEP 2: identity.py:"
       " fingerprint(map_a, map_b, rarity) -> (agree_weight, total_weight, n_values): pair the two maps'"
       " to_flat() with align+pair_moved (as memory._match does); keep only pairs where both texts are"
       " non-empty and labels.element_type(e) is NOT in labels.FIXABLE_TYPES and is not 'control'"
       " (data-shaped values only); weight of a value = 1 / rarity[(link, shape, text)], rarity = number of"
       " sessions whose map of that link shows that (shape, text); an equal pair adds its weight to agree"
       " and total; an unequal pair adds the mean of both weights to total. similarity = agree/total."
       " decide(sim, n): n >= MIN_VALUES (3) and sim >= SAME (0.9) -> 'same'; n >= 3 and sim <= DIFF (0.5)"
       " -> 'different'; else 'undecided'. resolve_owners(session_maps, session_ids) -> {session: owner}:"
       " start from group_clients(session_ids); for every session with NO ids, compare it with every other"
       " session sharing a page link (sum agree/total/n over the shared links): any 'same' -> take that"
       " session's owner (chain like group_clients, union-find); 'different' from every session it shares"
       " a link with -> its own client 'client <n>' (next free number); otherwise owner 'undecided"
       " <session>'. Sessions with ids keep their group_clients owner. STEP 3: memory.client_maps() uses"
       " resolve_owners and DROPS 'undecided ...' owners from memory (no vote); the regression runner"
       " prints owners per session. Ask the user D4 before coding the 'undecided' branch:"
       f" `{PY} tools/sdis.py ask W1-3 --question \"A capture SDIS cannot identify (no PAN/GSTIN, data"
       " inconclusive): what should happen to it?\" --options \"No vote|Low-weight vote\" --default \"No"
       " vote\"` and follow the ANSWER (low weight = count it as 0.5 of a client in confirmed() and"
       " relevance; record how). STEP 4: tests/test_sdis_identity.py: fixtures A and B as two sessions with"
       " no ids -> 'different' -> two clients; A twice (two sessions) -> 'same' -> one client; a session"
       " with too few data values -> 'undecided'; chained ids still join. Regression: the two fictional"
       " sessions (no ids, same fictional page) become ONE client, so the fictional pages drop out of the"
       " memory section (one client); the two real GST clients stay two. Explain this in the note."),

    wp("W1-4", 1, "sonnet", "M", ["W0-2"], "Part N: smart page link resolution",
       "Blueprint Part N. core/sdis/links.py: resolve(links_by_client: Dict[str, Set[str]]) ->"
       " Dict[str, str] mapping every link to its resolved link. A link is 'host/path#route' as"
       " keys.page_link() makes it. Split it into host + segments (path segments, then the route's"
       " segments after '#'). Group links by (host, number of segments, whether a route exists). Inside a"
       " group, look at every PAIR of links that differ in exactly ONE segment position i: if the two"
       " links belong to DIFFERENT clients (and not to a common client) that is one vote 'value at i'; if"
       " the same client has both, that is one vote 'page at i'. Position i is masked in that group when"
       " value votes >= 2 and page votes == 0: every link's segment i becomes '{v}'. Never mask the host,"
       " never mask a segment of letters only that the same client also visited with another value."
       " Links not in any masked group map to themselves. Do NOT use core/sgt_i atlas.url_hint. Wire it"
       " in: link_map.build_maps takes an optional link_of mapping (raw link -> resolved link) and keys maps"
       " by the resolved link; memory.client_maps computes the clients' raw links first (from the session"
       " maps it already builds), calls resolve(), then builds again with the mapping. Tests"
       " (tests/test_sdis_links.py): /returns/2026-27/summary for 3 clients -> one resolved link;"
       " /returns/gstr1 and /returns/gstr2b visited by the same client -> unchanged; a value position seen"
       " for only one client -> unchanged; a route segment ('#/auth/ARN123') resolved the same way."
       " Regression: no change expected on the real captures (their links carry no values); if one"
       " changes, explain."),

    wp("W1-5", 1, "sonnet", "S", ["W0-2"], "Part M: per-browser memory",
       "Blueprint Part M. Every record may carry 'browser' (chrome / msedge / firefox / ''): the pre-dev"
       " captures have none, so they are browser ''. STEP 1: tools/pre_dev/class_diff/key_probe.py: when it"
       " saves a capture or read, add 'browser' = the window process's exe name without .exe, lower case"
       " (copy the OpenProcess / QueryFullProcessImageNameW approach of"
       " core/vsdc/vsdc_router.py get_foreground_info, for the hwnd key_probe already has). STEP 2:"
       " link_map.all_sources() passes the browser along (add it to the record dict it yields, key"
       " 'browser'); LinkMap gets attribute browser (from the first read). memory.client_maps() groups"
       " by (link, browser) - a PageMemory gets attribute browser - so pages are only compared with the"
       " same browser. The printout shows the browser when it is not ''. (Joining datapoints ACROSS"
       " browsers happens in W3-2.) Tests: two fixture reads of one link marked chrome and firefox ->"
       " two memories; both '' -> one. Regression unchanged."),

    wp("W1-R", 1, "opus", "S", ["W1-1", "W1-2", "W1-3", "W1-4", "W1-5"], "Phase 1 review",
       "Review W1-1..W1-5 against blueprint section 0 and Parts B, D, G, M, N and their hand-off notes."
       " Check: the order in which link resolution (N), screens (G), browser (M) and identity (D) are"
       " applied in client_maps is consistent (resolve links -> identity -> per (link, browser) -> screens);"
       " no vote is ever counted per snapshot (R8); the regression numbers changed only where the notes"
       " explain it; the tests are meaningful. Fix defects with tests. Run the regression runner and all"
       " tests/test_sdis_*.py.", kind="review"),

    wp("W2-1", 2, "sonnet", "L", ["W1-R"], "Part E: look-alikes scored, AMBIGUOUS",
       "Blueprint Part E. Implement in core/sdis/align.py exactly this (it was written once and passed all"
       " tests): AMBIGUOUS_MARGIN = 0.5; _MAX_CELLS = 40000. _pattern(text): collapse whitespace, digits ->"
       " '9', letters (regex [^\\W\\d_]) -> 'A', then collapse runs of the same char (re.sub(r'(.)\\1+',"
       " r'\\1', t)). _column(a, b): sideways overlap of node rects ([x, y, w, h], from e['node']['rect'])"
       " divided by the smaller width, 0.0 when a rect is missing or a width <= 0. _score(a, b) = (1.0 if"
       " _pattern equal else 0.0) + _column. _best_injection(score, banned=None): score is k rows x n"
       " columns (k <= n); DP dp[i][j] = max(dp[i][j-1], dp[i-1][j-1] + s) with s = -inf for the banned"
       " (row, col); every row must be paired, in order; return (total, chosen column per row); if the"
       " total is -inf return (-inf, []) WITHOUT backtracking. _look_alikes(xs, ys, fl, fp, pairs,"
       " ambiguous): rows = the shorter side; best = _best_injection(score); a row r is UNSURE when best -"
       " _best_injection(score, (r, chosen[r]))[0] < AMBIGUOUS_MARGIN; if k*n*k > _MAX_CELLS mark every row"
       " unsure without the extra runs; write the pairs (map back to fl/fp indexes when the shorter side"
       " was fp) and add the fl index to `ambiguous` when unsure and the fl element has text. In align():"
       " add parameter ambiguous: Optional[set] = None; inside each gap between anchors, group ga and gb by"
       " shape; shapes present on both sides with DIFFERENT counts are 'uneven'; pair the LCS-by-shape as"
       " today but skip pairs whose shape is uneven; then call _look_alikes for each uneven shape. Equal"
       " counts behave exactly as today. Wire-up: memory._match passes an ambiguous set; an observation"
       " paired ambiguously is stored with c['sure'] = False (a later sure observation of the same client"
       " sets it True); confirmed() counts only clients with sure True; verdict returns 'ambiguous' for a"
       " node whose clients include a not-sure one and that is not confirmed. compare.compare_flat passes"
       " a set too and sets check = 'ambiguous pairing - look-alikes, one side has fewer' for those rows."
       " link_map (same-client merge) does not pass a set. Tests (tests/test_sdis_lookalikes.py) on"
       " hand-made flats (dicts with key, text, node: {rect}): equal counts -> no ambiguous; 2 vs 1 with the"
       " single one matching the SECOND by pattern -> paired with the second, not ambiguous; 2 vs 1 with"
       " identical patterns and columns -> ambiguous; _pattern examples ('AB1234' -> 'A9', 'Filed' -> 'A')."
       " All 14 old tests must still pass and the merge numbers (double count, lost) must stay 0."),

    wp("W2-2", 2, "sonnet", "M", ["W1-R"], "Part C: noise over time",
       "Blueprint Part C (rules in order). core/sdis/noise.py with functions on a PageMemory node:"
       " changes_within_client(node) (exists today in memory.verdict: any client with 2+ texts; move it"
       " here); changes_with_time(node): build {day: set of texts} from every client's history (day ="
       " history.day_of(stamp), the LAST text that client had on that day); true when there are 2+ days,"
       " every day's set has exactly one text, and the texts differ between days; probably_furniture(mem,"
       " nid): the node's verdict is 'differs between clients', labels.value_type(text) is 'sentence' or"
       " 'label', and it has NO label: until W3-1 brings real labels, 'no label' = none of the 6 nodes"
       " before it in mem.order is confirmed 'same for all clients' with a letter in its text"
       " (LABEL_LOOKBACK = 6; leave a TODO naming W3-1). memory.verdict order: retired (later), composite,"
       " 'changes within one client', 'changes with time', then the existing ones; 'probably furniture'"
       " replaces 'differs between clients' when it applies. Ask D5 first:"
       f" `{PY} tools/sdis.py ask W2-2 --question \"SDIS spots furniture (dates, notices) by seeing pages on"
       " different days. Where should those captures come from?\" --options \"Automatic, from SDIS's"
       " recorder|Captured by hand\" --default \"Automatic, from SDIS's recorder\"` (the answer only goes"
       " in the note and blueprint; the code is the same). Tests (tests/test_sdis_noise.py) with hand-made"
       " PageMemory nodes: same text for 2 clients on day 1, another shared text on day 2 -> changes with"
       " time; different texts for 2 clients on the same day -> not; a long unlabelled sentence that"
       " differs -> probably furniture; the same with a label before it -> stays 'differs'. Regression:"
       " expect few or no changes (all captures are from one day); explain any."),

    wp("W2-3", 2, "sonnet", "M", ["W2-1"], "Part F: the variable_alignment state",
       "Blueprint Part F. core/sdis/memory.py verdict: a node whose verdict would be 'same for all"
       " clients' becomes 'variable_alignment' when ALL of: labels.value_type(text) != 'label' (not ending"
       " in ':'), it is not composite, its ctype is not a choice control (keys.CHOICE_CTYPES), and at least"
       " one OTHER node with the same shape (align.shape) has the verdict 'differs between clients'"
       " (compute the set of 'differs' shapes once per call, not per node). In"
       " tools/pre_dev/class_diff/compare.py add VARIABLE_ALIGNMENT = 'variable_alignment' to STATUSES; in"
       " compare_flat, after statuses are set, a FIXED element whose shape is shared by a VARIABLE element"
       " (same exclusions) becomes VARIABLE_ALIGNMENT, and the label set ('shared') still includes it"
       " (row names like 'Cash ledger' must stay labels). Add a 'rejected' hook: memory accepts an optional"
       " set of rejected node signatures (link, shape, text) - a rejected one is plain 'same for all"
       " clients' again (W4-4 stores rejections). Tests: tests/test_class_diff_align.py must still pass"
       " unchanged (every value keeps its label); new tests: fixtures A/B give at least one"
       " variable_alignment (the 'Filed' status cells), and a rejected signature turns it back. Regression:"
       " print the variable_alignment count per page; explain the numbers."),

    wp("W2-4", 2, "sonnet", "S", ["W1-R"], "Part H: memory upkeep (retire)",
       "Blueprint Part H (formula and worked examples exact). PageMemory keeps self.clients (the order"
       " clients were added); each node keeps first_ci (index of the client that created it) and last_ci"
       " (index of the last client that had it). After each add(): for every node in self.order that is"
       " confirmed: chances = len(self.clients) - first_ci; seen = number of clients that had it; misses ="
       " len(self.clients) - 1 - last_ci; p = (seen + 1) / (chances + 2); retire when misses >= 2 and"
       " (1 - p) ** misses < RETIRE_P (0.01): remove it from self.order, status 'retired', verdict"
       " 'retired'. Never retire an unconfirmed node. A retired node that a later client matches through"
       " pending simply comes back as a new pending node (nothing special). Tests"
       " (tests/test_sdis_retire.py): a node in 10 clients then missing in 4 -> retired; missing in 3 ->"
       " not; a node in 2 clients then missing in 4 -> not retired; an unconfirmed node never retires."
       " Regression unchanged (2 clients cannot retire anything); say so."),

    wp("W2-R", 2, "opus", "S", ["W2-1", "W2-2", "W2-3", "W2-4"], "Phase 2 review",
       "Review W2-1..W2-4 against section 0 and Parts C, E, F, H. Check the verdict order in memory is one"
       " clear function (retired, composite, noise, ambiguous, waiting, variable_alignment, probably"
       " furniture, same/differs) and is documented in its docstring; client order still changes no"
       " verdict (the runner's 'orders agree'); thresholds are module constants named as in D11. Ask"
       f" D11: `{PY} tools/sdis.py ask W2-R --question \"Keep SDIS's starting thresholds (screen 0.5, retire"
       " 1%, identity 0.9/0.5 over 3 values, ambiguity margin 0.5) until the results show a reason to"
       " change?\" --options \"Keep them|Let me set them\" --default \"Keep them\"`. Fix defects with"
       " tests.", kind="review"),

    wp("W3-1", 3, "sonnet", "L", ["W2-R"], "Part I: statuses + labels from memory",
       "Blueprint Part I. STEP 1: move the label logic from tools/pre_dev/class_diff/compare.py into"
       " core/sdis/labels.py unchanged: _container, _children, _first_label, _same_column, _table_label,"
       " LABEL_LOOKBACK, _label (compare imports them back; tests/test_class_diff_align.py must pass"
       " unchanged). STEP 2: PageMemory keeps, per client, its last view: (flat, nid_of_idx) set in add()"
       " (index in that client's flat -> node id). STEP 3: memory.status(nid) maps verdicts to the"
       " dialog's statuses: 'same for all clients' + labels.FIXABLE_TYPES type -> fixed; 'same for all"
       " clients' + data type -> semi-variable; 'differs between clients' -> variable; variable_alignment;"
       " ambiguous; noise verdicts and probably furniture -> furniture; unconfirmed -> waiting; retired;"
       " composite. STEP 4: memory.label(nid): take the latest client view that holds the node, index i;"
       " the label candidates are the indexes whose node status is fixed or variable_alignment, or"
       " semi-variable with value_type 'alphanumeric' (as compare's 'shared'), minus composites, minus"
       " labels.NEVER_LABEL_CTYPES, and with a letter in the text; return labels._label(flat, i,"
       " candidates). Replace W2-2's temporary 'no label' check with label(nid) == ''. STEP 5:"
       " compare.py main(): the default now writes, per page memory, one CSV row per node with text:"
       " status, label, value type, clients, example value (latest client), key; the old two-client mode"
       " stays under --two. STEP 6: tests (tests/test_sdis_labels.py): build memory from fixtures A and B"
       " and check every value in tests/test_class_diff_align.py's EXPECTED gets its label from"
       " memory.label (import EXPECTED from that test module). Regression: print status counts per page."),

    wp("W3-2", 3, "sonnet", "L", ["W3-1"], "Part J: relevance by occurrences + slots",
       "Blueprint Part J (counting rules exact). core/sdis/relevance.py: datapoints(memories, rejected=())"
       " -> list of Datapoint (dataclass: key, label, value_type, relevance_pct, sure_pct, pages: list of"
       " (link, screen, browser, share), nodes: list of (memory index, nid), slot_type_pct, surprise). A"
       " node counts when its status is variable or semi-variable (data, R2) - fixed, furniture,"
       " ambiguous, retired, composite and rejected variable_alignment do not. Its page share = clients"
       " that have it (sure observations) / clients in that PageMemory. Datapoints are JOINED across pages"
       " and browsers by key = (label lower-cased, trailing ':' and spaces removed, value type); a node"
       " with no label is its own datapoint keyed by (link, shape). relevance = sum of page shares;"
       " relevance_pct = round(100 * relevance / max relevance). sure_pct = round(100 * min(1, distinct"
       " clients / FULL_SURE_CLIENTS)) with FULL_SURE_CLIENTS from D3. Slots: the value types of all the"
       " datapoint's values -> slot_type_pct = share of the most common type; surprise = True when that"
       " share is >= 90% but some value has another type. Ask D3 and D8:"
       f" `{PY} tools/sdis.py ask W3-2 --question \"How many different clients should a page have before"
       " SDIS shows a datapoint as fully sure (100%)?\" --options \"2|5|10\" --default \"5\"` and"
       f" `{PY} tools/sdis.py ask W3-2 --question \"Should your picks and rejections in the Distill dialog"
       " push similar datapoints up or down next time?\" --options \"Yes|No\" --default \"Yes\"` (if Yes:"
       " datapoints() takes picked keys and rejected keys; a picked key gets x1.5, a rejected key is left"
       " out). Print in memory.py main() and the regression runner: number of datapoints and the"
       " relevance_pct of the top 10 (numbers only, no labels). Tests (tests/test_sdis_relevance.py):"
       " fixed nodes never become datapoints; a value on 2 pages with the same label joins into one"
       " datapoint with 2 pages; a 50-row list counts once per client; share uses the page's own client"
       " count; slots and surprise."),

    wp("W3-3", 3, "sonnet", "L", ["W3-2"], "Part O engine: memory on disk, incremental mining",
       "Blueprint Part O (engine side only, no UI, no subprocess yet). STEP 1: core/sdis/store.py:"
       " save(state, path) / load(path) for the whole mining state as gzip JSON at data_dir() /"
       " 'memory.json.gz', written atomically (write a .tmp, then os.replace): every PageMemory (all its"
       " fields, as plain lists/dicts), the owners, the resolved-link mapping, the processed sources"
       " ({file name: [size, mtime]} plus per-file the stamps already taken), the rejected/picked keys and"
       " the time of the last run. A STATE_VERSION int; a different version means 'rebuild'. STEP 2:"
       " core/sdis/mine.py: mine(captures_dir, state_path=None, progress=None, cancel=None, rebuild=False)"
       " -> summary dict. It loads the state, lists the sources, takes only sources not yet processed,"
       " groups them into client maps the same way client_maps() does (links resolved over ALL links seen"
       " so far, identity over all sessions so far), and adds ONE client map at a time to the right"
       " PageMemory, calling progress(done, total, page_link) after each and saving the state after each"
       " (so Cancel loses at most one). cancel is a threading.Event-like object checked between clients."
       " A client already in a PageMemory that gets new pages is added again: votes are per client, so"
       " this is safe (PageMemory.add must not double-count it: test it). If the owners or link mapping"
       " of an ALREADY-ADDED session change, rebuild that page from scratch (log it). Then relevance is"
       " recomputed and stored. STEP 3: memory.py main() and the regression runner get --state to use"
       " mine(). Tests (tests/test_sdis_mine.py, tmp_path): mine twice -> the second run processes 0; add"
       " one capture -> processes 1; the verdict counts after incremental runs equal a fresh rebuild;"
       " cancel after the first client -> state saved with one client; a corrupt or old-version state ->"
       " rebuild. Regression: identical numbers through mine()."),

    wp("W4-1", 4, "opus", "L", ["W3-3"], "Part K: SDIS's own recorder (raw view, session id, browser)",
       "Blueprint Part K and rule 8 (this WP is the one place SGT is touched; SGT's own reads and results"
       " must not change). STEP 1: core/sdis/recorder.py SdisRecorder(directory=None, enabled=True,"
       " budget_s=1.5, cap_bytes=500*1024*1024, read_nodes=None, browser_of=None): offer(hwnd, session,"
       " portal, url, title) puts ONE pending job in a size-1 slot (a newer offer replaces an older one"
       " not yet taken) and returns at once - it must never block or raise. A daemon worker thread takes"
       " jobs: reads core.sgt_i.uia_nodes.read_page_nodes(hwnd, timeout_sec=budget_s, raw_view=True)"
       " (initialise COM on that thread the way core/sgt_i does for its own UIA thread - grep"
       " CoInitialize in core/sgt_i), measures the time, DROPS the record if it took longer than budget_s"
       " or came back empty, else appends one JSON line {v: 1, ts (ISO), session, portal, url, link"
       " (keys.page_link(url)), title, browser, docs} to sdis_YYYY-MM-DD.jsonl in directory (default:"
       " Path.home()/SERA_DATA_DIR_NAME/'sdis_capture' - NOT the admin corpus folder). browser_of(hwnd)"
       " = the window process exe name without .exe, lower case (same approach as"
       " core/vsdc/vsdc_router.py get_foreground_info). After each write, if the folder is bigger than"
       " cap_bytes, delete the oldest files until it is not (never the file being written). STEP 2:"
       " core/sgt/sgt_shadow.py: SgtShadow.__init__ gets sdis=None (keep it as self._sdis); in _observe,"
       " right AFTER the existing `if self._recorder is not None:` block (so after SGT's own read), add:"
       " `sdis = self._sdis` / `if sdis is not None and sdis.enabled: sdis.offer(hwnd, s.session_id,"
       " portal, url, title)` wrapped so nothing can raise. Nothing else in _observe changes. STEP 3:"
       " core/vsdc/vsdc_router.py builds SdisRecorder next to PageRecorder when the setting is on: new"
       " setting key 'sdis_record' (default '1'), a checkbox 'Record pages for Sera Distill (SDIS)' in"
       " ui/dialogs/unified_settings_dialog.py on the same page as 'Record pages for SGT testing'"
       " (grep it). STEP 4: core/sdis/sources.py: read_records(directory) yields the (stamp, session,"
       " link, rec) tuples link_map expects (stamp = ts, rec = {'docs': ..., 'browser': ...}); mine()"
       " accepts a recorder folder as well as pre-dev captures (detect by file name). Ask D13:"
       f" `{PY} tools/sdis.py ask W4-1 --question \"SDIS's extra page read: drop it when it takes longer"
       " than how long, and how much disk may it use on each staff PC?\" --options \"1.5 s and 500"
       " MB|1 s and 250 MB|3 s and 1 GB\" --default \"1.5 s and 500 MB\"`. Tests"
       " (tests/test_sdis_recorder.py, fakes only, no real UIA): offer returns in < 5 ms with a reader that"
       " sleeps 2 s; a slow read is dropped; a fast one is written with every field; the size cap deletes"
       " the oldest file; a raising reader never escapes; SgtShadow with a stub sdis gets one offer per"
       " changed page and none when unchanged (follow tests/test_sgt_shadow*.py setups). Then run the SGT"
       f" tests (`{T} tests/test_sgt_*.py`) and `{PY} tools/sgt_replay.py diff` - it must show no"
       " change. Add a hands-on check: `check-add W4-1 --text \"Real Chrome, Edge and Firefox on a GST"
       " page: sdis_capture gets one record per changed page with the right browser; SGT capture behaves"
       " as before\"`."),

    wp("W4-2", 4, "opus", "L", ["W4-1"], "Part P: captures travel to the admin PC",
       "Blueprint Part P (protocol exact). Ask D14 first:"
       f" `{PY} tools/sdis.py ask W4-2 --question \"How should SDIS captures reach the admin PC?\" --options"
       " \"Push over Sera Sync's secure channel, delete after the admin confirms|Shared folder\" --default"
       " \"Push over Sera Sync's secure channel, delete after the admin confirms\"` (if 'Shared folder':"
       " block the WP with the reason 'needs a re-plan' and stop). STEP 1: core/sdis/transfer.py:"
       " FRAME_PUSH = 'sdis_push'; push(session, files) (staff side): send {t: 'sdis_push', files: [{name,"
       " size, sha256}]}, then each file with session.send_file, then read {t: 'sdis_ack', files: [...]}"
       " and return the acked names; handle_push(session, app_dir) (admin side): refuse (send {t:"
       " 'sdis_ack', files: []}) unless this PC is the admin PC (sync_admin.is_admin_pc(app_dir)); else"
       " recv_file each into core.sdis.paths.data_dir()/'corpus'/<peer device id>/<name> (session has the"
       " peer device id), check the SHA-256 (recv_file returns it), ack only the files whose hash matches."
       " Never log contents. STEP 2: sync_office.dispatch_session: add `elif t == 'sdis_push':` ->"
       " core.sdis.transfer.handle_push (lazy import). STEP 3: staff side trigger: find where the app"
       " connects to the admin PC for sync (grep sync_engine.py for how it opens a client Session to a"
       " peer, and main.py for the sync engine's start and its timer); after each successful sync round,"
       " or at most every 15 minutes, in a background thread: list finished sdis_capture files (not"
       " today's open file), push them, and delete exactly the acked ones. On the admin PC itself the"
       " recorder's folder is read by mine() directly (no push). STEP 4: tests"
       " (tests/test_sdis_transfer.py): a loopback mutual-TLS session the way tests/test_sync_transport*.py"
       " builds one; push two files -> both acked and stored under corpus/<device>; a corrupted file ->"
       " not acked and not deleted on the staff side; a non-admin receiver acks nothing. Hands-on check:"
       " `check-add W4-2 --text \"Two real office PCs: a staff PC's sdis_capture files arrive on the admin"
       " PC and are deleted on the staff PC only after arriving\"`."),

    wp("W4-3", 4, "opus", "M", ["W3-3"], "Part O: mining process capped at 10% CPU",
       "Blueprint Part O (Job Object details exact). STEP 1: core/sdis/jobcap.py, ctypes only:"
       " cap_current_process(percent=10) creates a Job Object (CreateJobObjectW), sets"
       " JOBOBJECT_CPU_RATE_CONTROL_INFORMATION (info class 15) with ControlFlags ="
       " JOB_OBJECT_CPU_RATE_CONTROL_ENABLE (0x1) | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP (0x4) and CpuRate"
       " = percent * 100, and assigns the CURRENT process (AssignProcessToJobObject(job,"
       " GetCurrentProcess())); returns True/False, never raises; no-op returning False off Windows. STEP"
       " 2: core/sdis/mine_process.py: run(argv) parses --captures, --state, --rebuild; calls"
       " cap_current_process(10) FIRST; runs mine() with progress printing one JSON line per step to"
       " stdout ({\"done\": n, \"total\": t, \"page\": link}, flushed) and a final {\"result\": \"ok\","
       " ...summary} or {\"result\": \"error\", \"message\": ...}; exit code 0/1. STEP 3: main.py: at the very"
       " top, right after `import sys` and BEFORE any PySide6, database or app import, add: if"
       " '--sdis-mine' in sys.argv: from core.sdis.mine_process import run; sys.exit(run(sys.argv)). Check"
       " the PyInstaller spec in build_tools/ (grep hiddenimports) and add core.sdis.mine_process if"
       " modules are listed by hand; do NOT build an installer. STEP 4: core/sdis/miner_client.py:"
       " MinerClient.start(captures, state) runs [sys.executable, '--sdis-mine', ...] when frozen"
       " (getattr(sys, 'frozen', False)) else [sys.executable, <repo>/main.py, '--sdis-mine', ...], with"
       " CREATE_NO_WINDOW; a reader thread parses the JSON lines and calls on_progress / on_done;"
       " cancel() terminates the process (the state on disk is safe: it is saved after every client)."
       " Tests (tests/test_sdis_mine_process.py): run(argv) on the fixtures in a tmp folder prints"
       " progress lines and a final ok; on Windows, a child that busy-loops for 3 s after"
       " cap_current_process(10) uses at most ~15% of (3 s x cpu count) CPU time (GetProcessTimes or"
       " psutil if it is installed; skip otherwise); MinerClient end to end against the fixtures. Hands-on"
       " check: `check-add W4-3 --text \"Find datapoints on the admin PC: Task Manager shows the mining"
       " process at or below 10% CPU; Cancel stops it and the next run continues\"`."),

    wp("W4-4", 4, "opus", "L", ["W3-3"], "Part Q: registration on every PC (synced tables)",
       "Blueprint Part Q and rule 8. Ask D7, D15 and D6 first (one question each):"
       f" `{PY} tools/sdis.py ask W4-4 --question \"When you pick a datapoint in Distill, should it become"
       " a field Sera captures on every PC (after your OK)?\" --options \"Yes, after my OK|No, only list"
       " it\" --default \"Yes, after my OK\"`;"
       f" `{PY} tools/sdis.py ask W4-4 --question \"Add a tracker column automatically for a registered"
       " datapoint?\" --options \"No (like the SGT lab)|Yes\" --default \"No (like the SGT lab)\"`;"
       f" `{PY} tools/sdis.py ask W4-4 --question \"Where should SDIS keep your edited labels and"
       " rejections?\" --options \"Office database (synced)|A file on the admin PC\" --default \"Office"
       " database (synced)\"`. STEP 1: tables sdis_fields (gid, name, portal, section, spec_json, label,"
       " status, created_by, updated_at) and sdis_decisions (gid, signature, decision, label, updated_at):"
       " create them where the office tables are created (sera_db/schema.py; follow tracker_dump) and"
       " register them for replication in sync_schema.py with _register(TableSpec(...)) exactly like"
       " tracker_dump; a sera_db mixin module sera_db/sdis.py with add/rename/retire/list functions (follow"
       " sera_db/tracker_dump.py and how database.py composes mixins). Run tests/test_sync_schema.py."
       " STEP 2: core/sdis/register.py: draft_spec(datapoint) uses core.sgt_i.miner.draft_field(label,"
       " typ, shapes, field, section, portal) - shapes = {value shape: count} where a value's shape maps"
       " letters to 'A' and digits to '9' per character (read miner.fill_shape / value_regex to match"
       " their shape format exactly); section: 'profile' unless the datapoint is in a list (records)."
       " write_fields_file(rows) writes the active rows, with the user's label, in sgt_fields.json format"
       " to <Sera data dir>/sdis_fields.json atomically. STEP 3: core/sgt/sgt_specs.py: SpecStore's default"
       " paths become [BUILTIN_FIELDS_PATH, override_path(), sdis_fields_path()] (a missing file is fine -"
       " _stamps already handles it); load_registry's default list too. STEP 4: rewrite the file at"
       " start-up and whenever the tables change (find the hook main.py runs after sync applies remote"
       " changes; also call it after a local add/rename). Renaming changes only the label; the spec's"
       " field name stays. Tests: tests/test_sdis_register.py (draft a spec from a fake datapoint and"
       " load it through load_registry; rename keeps the field; empty table -> no file or empty file);"
       f" `{T} tests/test_sgt_*.py tests/test_sync_schema.py` and `{PY} tools/sgt_replay.py diff` with an"
       " empty table -> no change. Note in the hand-off that main has uncommitted edits to sync_schema.py"
       " and sera_db/schema.py from other work, so the merge must be checked there."),

    wp("W4-5", 4, "sonnet", "L", ["W3-2", "W4-3", "W4-4"], "Part L: the Distill dialog + loading dialog",
       "Blueprint Part L and Part O (UI). STEP 1: ui/windows/tracker_dump_window.py: in"
       " _show_preferences_menu (the 'Tools  ▾' button) add 'Distill…' (icon mdi.flask-outline). On a"
       " PC that is not the admin PC (db.is_admin_pc()) show a QMessageBox 'Sera Distill runs on the"
       " admin PC only.' and stop. STEP 2: ui/dialogs/sdis_dialog.py, a non-modal QDialog, readable and"
       " calm (NOT the SGT lab's dense screen; reuse the colours of tracker_dump_window's stylesheet):"
       " top bar = 'Find datapoints' button + 'Last run: <time>' + filters (search box, Relevance >= x%,"
       " page, browser, state); main area = QTreeWidget, one top-level row per Datapoint (columns: Label,"
       " Relevance %, Sure %, Type, Found on (n pages)), its child rows = the pages it was found on"
       " (collapsible arrow, R5); the Label cell is editable by double-click (R6) and the edit is saved at"
       " once (sdis_decisions / sdis_fields via the W4-4 functions) and NEVER overwritten by a later run;"
       " a 'Register' button for the selected datapoint (asks 'Capture <label> on every PC?' then W4-4's"
       " register); a second tab 'Please check' listing variable_alignment items with Keep / Reject"
       " buttons (Reject is saved and sent to the next mining run); every number shown is a percentage"
       " (R7). STEP 3: the loading dialog: an APPLICATION-MODAL QDialog (locks the app, not Windows) with a"
       " progress bar, 'Working on <done> of <total> - <page link>' and a Cancel button, driven by"
       " core/sdis/miner_client.MinerClient through Qt signals (never block the UI thread); on done it"
       " reloads the results from the saved state. Opening the dialog never starts mining. Tests"
       " (tests/test_sdis_dialog.py, QT_QPA_PLATFORM=offscreen like tests/test_sgt_i_lab.py): the dialog"
       " builds from a fake state with 3 datapoints; a datapoint row has its page children; editing a"
       " label calls the save function; the non-admin path shows the message (monkeypatch"
       " QMessageBox). Hands-on check: `check-add W4-5 --text \"Admin PC: Tracker dump -> Tools ->"
       " Distill...: Find datapoints shows the loading dialog and locks the app; Cancel works; rename a"
       " label, register a datapoint, and see it captured on another PC after sync\"`."),

    wp("W5-R", 5, "opus", "M", ["W4-2", "W4-5"], "Final review and merge-readiness note",
       "Review the whole branch against blueprint sections 0 and 9 and every Part. Run all"
       f" tests/test_sdis_*.py, tests/test_class_diff_align.py, tests/test_sgt_*.py, tests/test_sync_*.py"
       f" and `{PY} tools/sgt_replay.py diff` (no change); run the regression runner and compare with"
       " docs/sdis/sdis-regress-baseline.txt, explaining every difference. grep core/sdis for any print or"
       " log of a text/value (only counts allowed). Fix defects with tests. Write the merge-readiness note"
       " in section 11 of the blueprint (what is built, what changed for SGT, the files on main that will"
       " conflict - sync_schema.py, sera_db/schema.py, main.py, unified_settings_dialog.py,"
       " tracker_dump_window.py have uncommitted edits on main - and the hands-on checks to run before"
       " merging).", kind="review"),
]


def main() -> None:
    base = json.loads(AUTOFILL.read_text(encoding="utf-8"))
    plan = {
        "about": ("Sera Distill (SDIS) work packages (blueprint docs/sdis/sdis-blueprint.md). Fixed data:"
                  " re-plan by editing docs/sdis/make_plan.py and running it. Live status is in"
                  " sdis-status.csv, written only by tools/sdis.py."),
        "deadline": "2026-10-04T23:00:00+05:30",
        "models": {k: v for k, v in base["models"].items() if v["runner"] == "claude"},
        "tiers": {"_note": "Claude only (owner decision 2026-09-29).", "sonnet": ["sonnet"],
                  "haiku": ["haiku"], "opus": ["opus"]},
        "runners": {"claude": base["runners"]["claude"]},
        "cli": base["cli"],
        "wps": WPS,
    }
    ids = {w["wp"] for w in WPS}
    for w in WPS:
        assert all(d in ids for d in w["deps"]), w["wp"]
    (HERE / "sdis-plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(WPS)} WPs written")


if __name__ == "__main__":
    main()
