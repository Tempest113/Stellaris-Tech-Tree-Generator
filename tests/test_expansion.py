"""Variable resolution and inline_script expansion.

The corpus tests here guard the single most expensive defect class in this
problem domain: fields that exist *only after expansion*. Every count is a
measured fact, and several are paired with a deliberate "before" assertion so
the test fails if expansion silently stops happening.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.clausewitz import Block, Scalar, parse, serialize
from pipeline.inline_scripts import (
    Expander,
    InlineScriptError,
    ScriptIndex,
    build_index,
)
from pipeline.loadorder import LoadOrder, base_game_source, merge_keys, mod_source, resolve_files
from pipeline.steam import Install
from pipeline.variables import VariableTable, collect_variables

#: Technologies that carry a ``levels`` field once expansion has run.
#: 26 vanilla + 62 Gigastructures. The previous attempt shipped a predicate that
#: found 76 of these, because it tested ``levels < 0`` and so missed every
#: finite-level repeatable.
REPEATABLE_COUNT = 88

#: Technologies whose entire body arrives from ``technology/giga_mega_repeatable``.
TEMPLATE_EMITTED_COUNT = 50

CANARY = "giga_tech_repeatable_vanilla_dyson_cap"


@pytest.fixture(scope="module")
def corpus(install: Install, gigas_root: Path):
    """Vanilla + Gigastructures, both raw and fully expanded."""
    load_order = (
        LoadOrder()
        .add(base_game_source(install.game, install.version))
        .add(mod_source(gigas_root))
    )
    raw = merge_keys(resolve_files(load_order, "common/technology"))
    variables = collect_variables(load_order, extra=raw.inline_variables)
    expander = Expander(build_index(load_order))
    expanded = {
        key: variables.substitute(expander.expand(block)) for key, block in raw.blocks.items()
    }
    return {
        "raw": raw,
        "variables": variables,
        "expander": expander,
        "expanded": expanded,
    }


# --------------------------------------------------------------------------
# Variables — unit
# --------------------------------------------------------------------------


def test_variable_may_hold_a_string_not_a_number():
    table = VariableTable()
    table.declare("giga_amb_flag", "giga_buildcap_j")
    tree = parse("potential = { has_global_flag = @giga_amb_flag }")
    resolved = table.substitute(tree.get_first("potential"))
    assert resolved.scalar_text("has_global_flag") == "giga_buildcap_j"
    assert table.as_number("@giga_amb_flag") is None


def test_unknown_variable_is_left_intact_and_recorded():
    """ACOT cost variables are absent unless ACOT is loaded; guessing would lie."""
    table = VariableTable()
    assert table.resolve_text("@acot_tier6cost2") == "@acot_tier6cost2"
    assert "acot_tier6cost2" in table.missing


def test_variable_indirection_is_followed():
    table = VariableTable()
    table.declare("a", "@b")
    table.declare("b", "7")
    assert table.lookup("a") == "7"


def test_variable_cycle_does_not_hang():
    table = VariableTable()
    table.declare("a", "@b")
    table.declare("b", "@a")
    assert table.lookup("a") is None


def test_substitution_rebuilds_rather_than_mutates():
    table = VariableTable()
    table.declare("x", "5")
    original = parse("a = @x")
    copy = table.substitute(original)
    assert original.scalar_text("a") == "@x"
    assert copy.scalar_text("a") == "5"


# --------------------------------------------------------------------------
# inline_script — unit
# --------------------------------------------------------------------------


def _index(tmp_path: Path, **scripts: str) -> ScriptIndex:
    index = ScriptIndex()
    for name, body in scripts.items():
        path = tmp_path / f"{name}.txt"
        path.write_text(body, encoding="utf-8")
        index.files[name.lower()] = path
    return index


def test_shorthand_and_block_forms_both_expand(tmp_path: Path):
    index = _index(tmp_path, thing="modifier = { factor = 2 }")
    expander = Expander(index)
    for source in ('a = { inline_script = "thing" }', "a = { inline_script = thing }"):
        result = expander.expand(parse(source).get_first("a"))
        assert result.block_at("modifier").scalar_text("factor") == "2"


def test_parameters_substitute_mid_token(tmp_path: Path):
    """``giga_tech_repeatable_$name$_cap`` is why substitution is textual."""
    index = _index(tmp_path, tmpl="key_$name$_suffix = { title = \"t_$name$\" }")
    expander = Expander(index)
    result = expander.expand(
        parse("a = { inline_script = { script = tmpl name = dyson } }").get_first("a")
    )
    assert result.has("key_dyson_suffix")
    assert result.get_first("key_dyson_suffix").scalar_text("title") == "t_dyson"


def test_parameter_default_is_used_when_argument_absent(tmp_path: Path):
    index = _index(tmp_path, tmpl="a = $pc_continental|0$")
    expander = Expander(index)
    result = expander.expand(parse("x = { inline_script = tmpl }").get_first("x"))
    assert result.scalar_text("a") == "0"


def test_directive_nested_deep_inside_a_body_expands(tmp_path: Path):
    """Gigas calls scripts from inside weight_modifier, cost and technology_swap."""
    index = _index(tmp_path, boost="modifier = { factor = 3 }")
    expander = Expander(index)
    result = expander.expand(
        parse("t = { weight_modifier = { inline_script = boost } }").get_first("t")
    )
    assert result.block_at("weight_modifier").block_at("modifier").scalar_text("factor") == "3"


def test_expanded_items_splice_as_siblings_producing_duplicate_keys(tmp_path: Path):
    """``giga_tech_repeatable_furnace_cap`` ends with two weight_modifiers."""
    index = _index(tmp_path, tmpl="weight_modifier = { modifier = { factor = 0 } }")
    expander = Expander(index)
    result = expander.expand(
        parse(
            "t = { inline_script = tmpl weight_modifier = { modifier = { factor = 1 } } }"
        ).get_first("t")
    )
    assert len(result.get_all("weight_modifier")) == 2


def test_script_calling_a_script_expands_transitively(tmp_path: Path):
    index = _index(tmp_path, outer="inline_script = inner", inner="a = 1")
    expander = Expander(index)
    result = expander.expand(parse("t = { inline_script = outer }").get_first("t"))
    assert result.scalar_text("a") == "1"


def test_recursive_script_raises_rather_than_hanging(tmp_path: Path):
    index = _index(tmp_path, loop="inline_script = loop")
    with pytest.raises(InlineScriptError, match="cycle"):
        Expander(index).expand(parse("t = { inline_script = loop }").get_first("t"))


def test_commented_out_parameter_is_not_reported_as_unsupplied(tmp_path: Path):
    """Gigas documents an abandoned ``$OWNED_AND_RUINED_MEGA_LIST$`` in a comment."""
    index = _index(tmp_path, doc="a = 1\n# unused idea: $NEVER_SUPPLIED$\n")
    expander = Expander(index)
    expander.expand(parse("t = { inline_script = doc }").get_first("t"))
    assert expander.stats.unsupplied_parameters == set()


def test_genuinely_unsupplied_parameter_is_reported(tmp_path: Path):
    index = _index(tmp_path, tmpl="a = $REQUIRED$")
    expander = Expander(index)
    expander.expand(parse("t = { inline_script = tmpl }").get_first("t"))
    assert expander.stats.unsupplied_parameters == {"tmpl:$REQUIRED$"}


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_known_variables_resolve_to_expected_values(corpus):
    variables = corpus["variables"]
    assert variables.lookup("repeatableTechTier") == "5"
    assert variables.lookup("fallentechtier") == "5"
    assert variables.lookup("horizontechtier") == "3"
    assert variables.lookup("giga_amb_flag") == "giga_buildcap_j"
    assert variables.lookup("giga_tier9cost4") == "448000"


@pytest.mark.corpus
def test_expansion_is_clean(corpus):
    stats = corpus["expander"].stats
    assert stats.missing_scripts == set()
    assert stats.unsupplied_parameters == set()
    assert stats.directives_expanded > 300


@pytest.mark.corpus
def test_fifty_technologies_have_no_tier_before_expansion(corpus):
    """The 'before' half of the canary. If this stops being true, re-check why."""
    missing = [k for k, b in corpus["raw"].blocks.items() if not b.has("tier")]
    assert len(missing) == TEMPLATE_EMITTED_COUNT
    assert all(k.startswith("giga_tech_repeatable_") for k in missing)


@pytest.mark.corpus
def test_every_technology_has_a_tier_after_expansion(corpus):
    assert [k for k, b in corpus["expanded"].items() if not b.has("tier")] == []


@pytest.mark.corpus
@pytest.mark.parametrize(
    "field", ["tier", "area", "potential", "levels", "prerequisites", "cost_per_level"]
)
def test_expansion_adds_exactly_the_template_emitted_fields(corpus, field):
    before = sum(1 for b in corpus["raw"].blocks.values() if b.has(field))
    after = sum(1 for b in corpus["expanded"].values() if b.has(field))
    assert after - before == TEMPLATE_EMITTED_COUNT


@pytest.mark.corpus
def test_repeatable_membership_is_declares_levels_not_a_sign_test(corpus):
    """Membership rule: declares ``levels`` at all.

    ``levels < 0`` finds only the infinite ones and misses every capped
    repeatable, which is how the previous attempt arrived at 76 instead of 88.
    """
    expanded = corpus["expanded"]
    declares_levels = [k for k, b in expanded.items() if b.has("levels")]
    assert len(declares_levels) == REPEATABLE_COUNT

    infinite_only = [
        k for k in declares_levels if float(expanded[k].scalar_text("levels")) < 0
    ]
    assert len(infinite_only) < REPEATABLE_COUNT, "sign test must be visibly insufficient"


@pytest.mark.corpus
def test_finite_repeatable_caps_are_present(corpus):
    """Vanilla caps at 5; Gigastructures uses 20 and 40."""
    caps = {
        int(float(b.scalar_text("levels")))
        for b in corpus["expanded"].values()
        if b.has("levels")
    }
    assert -1 in caps
    assert {5, 20, 40} <= caps


@pytest.mark.corpus
def test_canary_technology_is_fully_materialised(corpus):
    """The whole body of this technology comes from a template."""
    raw = corpus["raw"].blocks[CANARY]
    assert raw.keys() == ["inline_script"], "canary should be empty before expansion"

    expanded = corpus["expanded"][CANARY]
    assert expanded.scalar_text("area") == "physics"
    assert expanded.scalar_text("tier") == "5"
    assert expanded.scalar_text("levels") == "-1"
    assert expanded.scalar_text("cost") == "96000"  # @giga_grand_megastructure_base_tech_cost
    assert expanded.has("potential")
    assert expanded.block_at("prerequisites").scalar_text is not None
    assert [s.value for s in expanded.block_at("prerequisites").scalars()] == [
        "tech_dyson_sphere"
    ]


@pytest.mark.corpus
def test_template_potential_uses_lowercase_not(corpus):
    """``giga_mega_repeatable`` writes ``not = {``; 50 technologies inherit it."""
    potential = corpus["expanded"][CANARY].block_at("potential")
    assert potential.has("not")
