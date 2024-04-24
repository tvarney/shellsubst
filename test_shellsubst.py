import pytest

from shellsubst import ShellSubst, UnterminatedFormatError, InvalidVariableNameError


@pytest.fixture
def subst():
    return ShellSubst(
        {
            "A": "a",
            "B": "b",
            "C": "c",
            "one": "1",
            "two": "2",
            "three": "3",
            "empty": "",
            "spaces": "   ",
            "long": "some words to make this work, generally for string ops",
        }
    )


@pytest.mark.parametrize(
    "template,expected",
    (
        # No replacements
        ("", ""),
        ("simple", "simple"),
        # Simple replacements
        ("$A", "a"),
        ("A: $A", "A: a"),
        ("one: '$one'", "one: '1'"),
        ("$one item", "1 item"),
        ("[$one, $two, $three]", "[1, 2, 3]"),
        # Escape sequences
        (r"\$escaped", "$escaped"),
        (r"This is escaped: \$", "This is escaped: $"),
        (r"Total: \$100", "Total: $100"),
        (r"\$\$\$\$", "$$$$"),
        # Bracket replacements - simple
        ("${one}", "1"),
        ("value: ${one}", "value: 1"),
        ("${one} item", "1 item"),
        ("[${one}, ${two}, ${three}]", "[1, 2, 3]"),
        # Bracket replacement - ':-' fmt spec
        ("${one:-one}", "1"),
        ("value: ${one:-one}", "value: 1"),
        ("${one:-one} item", "1 item"),
        ("[${one:-one}, ${two:-two}, ${three:-three}]", "[1, 2, 3]"),
        ("${empty:-one}", "one"),
        ("value: ${empty:-one}", "value: one"),
        ("${empty:-one} item", "one item"),
        ("[${empty:-one}, ${empty:-two}, ${empty:-three}]", "[one, two, three]"),
        ("${missing:-one}", "one"),
        ("value: ${missing:-one}", "value: one"),
        ("${missing:-one} item", "one item"),
        ("[${missing:-one}, ${mising:-two}]", "[one, two]"),
        # Bracket replacement - '-' fmt spec
        ("${one-one}", "1"),
        ("value: ${one-one}", "value: 1"),
        ("${one-one} item", "1 item"),
        ("[${one-one}, ${two-two}]", "[1, 2]"),
        ("${empty-one}", ""),
        ("value: ${empty-one}", "value: "),
        ("${empty-one} item", " item"),
        ("[${empty-one}, ${empty-two}]", "[, ]"),
        ("${missing-one}", "one"),
        ("value: ${missing-one}", "value: one"),
        ("${missing-one} item", "one item"),
        ("[${missing-one}, ${missing-two}]", "[one, two]"),
        # Posix fmt specs (errors)
        ("${one+one}", UnterminatedFormatError),
        ("${one:+one}", UnterminatedFormatError),
        ("${one=one}", UnterminatedFormatError),
        ("${one:=one}", UnterminatedFormatError),
        ("${one?one}", UnterminatedFormatError),
        ("${one:?one}", UnterminatedFormatError),
        ("${#one}", InvalidVariableNameError),
    ),
)
def test_simple_no_strops_no_err(
    subst: ShellSubst, template: str, expected: str | Exception
):
    subst.strict = False
    subst.posix_formats = False
    subst.allow_string_ops = False
    if type(expected) is str:
        assert subst.replace(template) == expected
    else:
        with pytest.raises(expected):
            assert subst.replace(template) is None
