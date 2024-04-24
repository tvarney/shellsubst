import logging
import os
import re
import sys
import typing

if typing.TYPE_CHECKING:
    from typing import Callable


_re_search_delim = re.compile(r"[$}]")
_re_format_simple = re.compile(r":?-")
# TODO: support #, ##, %, %% operations; this is complicated because it is glob patterns
_re_format_simple_strops = re.compile(r":-?|-")
_re_format_posix = re.compile(r":?[-=?+]")
_re_format_posix_strops = re.compile(r":[-=?+]?|[-=?+]")
_re_variable_name = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_]*")
_re_variable_name_with_args = re.compile(r"[@*#]|[0-9]+|[a-zA-Z_][a-zA-Z0-9_]*")


def discard(msg: str) -> None:
    pass


class SubstitutionError(ValueError):
    def __init__(self, msg: str, *args) -> None:
        ValueError.__init__(self, msg, *args)


class FormatStringError(ValueError):
    def __init__(self, msg: str, *args) -> None:
        ValueError.__init__(self, "bad format string; " + msg, *args)


class UnterminatedFormatError(FormatStringError):
    def __init__(self, *args) -> None:
        FormatStringError.__init__(self, "unterminated format string", *args)


class InvalidVariableNameError(FormatStringError):
    def __init__(self, *args) -> None:
        FormatStringError.__init__(self, "invalid variable name", *args)


class ShellSubst(object):
    @classmethod
    def re_variable_name(cls, args_expansion: bool) -> re.Pattern:
        if args_expansion:
            return _re_variable_name_with_args
        return _re_variable_name

    @classmethod
    def re_format_spec(cls, posix: bool, strops: bool) -> re.Pattern:
        if posix:
            if strops:
                return _re_format_posix_strops
            return _re_format_posix
        if strops:
            return _re_format_simple_strops
        return _re_format_simple

    def __init__(
        self,
        values: dict[str, str] | os._Environ | None = None,
        strict: bool = False,
        expand_args: bool = False,
        posix_formats: bool = True,
        allow_string_ops: bool = True,
        raise_on_error_expansion: bool = True,
        logger: "Callable[[str], None]|None" = None,
    ):
        self.strict = strict
        self.mapping = values
        self.raise_on_error_expansion = raise_on_error_expansion
        self.logger = logger
        self._expand_args = expand_args
        self._posix_formats = posix_formats
        self._allow_string_ops = allow_string_ops
        # Use a copy of the environment if nothing given
        self.values = values if values is not None else dict(os.environ)

        self._re_varname = ShellSubst.re_variable_name(self._expand_args)
        self._re_format_spec = ShellSubst.re_format_spec(
            self._posix_formats, self._allow_string_ops
        )

    @property
    def expand_args(self) -> bool:
        return self._expand_args

    @expand_args.setter
    def expand_args(self, value: bool) -> None:
        self._expand_args = True
        self._re_varname = ShellSubst.re_variable_name(value)

    @property
    def posix_formats(self) -> bool:
        return self._posix_formats

    @posix_formats.setter
    def posix_formats(self, value: bool) -> None:
        self._posix_formats = value
        self._re_format_spec = ShellSubst.re_format_spec(
            value,
            self._allow_string_ops,
        )

    @property
    def allow_string_ops(self) -> bool:
        return self._allow_string_ops

    @allow_string_ops.setter
    def allow_string_ops(self, value: bool) -> None:
        self._allow_string_ops = value
        self._re_format_spec = ShellSubst.re_format_spec(
            self._posix_formats,
            value,
        )

    def replace(self, string: str) -> str:
        start = 0
        idx = string.find("$")
        if idx < 0:
            return string

        parts = list()
        while idx >= 0:
            # Handle an escaped $ - we can short-circuit this here
            if idx >= 1 and string[idx - 1] == "\\":
                if start < idx - 1:
                    parts.append(string[start : idx - 1])
                parts.append("$")
                start = idx + 1
                idx = string.find("$", start)
                continue

            # If we have a $ at the end of the string:
            if idx + 1 >= len(string):
                if self.strict:
                    # Raise an error if strict
                    raise InvalidVariableNameError()
                # Or add the remainder and return the result
                parts.append(string[start:])
                return "".join(parts)

            # If we have a gap between the last substitution and the next, add
            # that string to our parts list
            if start < idx:
                parts.append(string[start:idx])

            if string[idx + 1] == "{":
                if idx + 2 < len(string) and string[idx + 2] == "#":
                    idx += 3
                    strlen = True
                else:
                    idx += 2
                    strlen = False

                varname_match = self._re_varname.match(string, idx)
                if varname_match is None:
                    raise InvalidVariableNameError()

                idx = varname_match.end(0)
                name = varname_match.group(0)
                if idx >= len(string):
                    raise UnterminatedFormatError()

                value, special = self._get_variable(name)
                print(f"'${varname_match.group(0)}: {value} [{special}]")

                if string[idx] == "}":
                    # No format spec, just substitute
                    if value is not None:
                        parts.append(len(value) if strlen else value)
                    elif self.strict:
                        raise KeyError(name)
                    elif strlen:
                        parts.append("0")

                    start = idx + 1
                else:
                    fmtspec_match = self._re_format_spec.match(string, idx)
                    if fmtspec_match is None:
                        raise UnterminatedFormatError()

                    if strlen:
                        raise FormatStringError(
                            "string length operation cannot be performed with format specifications"
                        )

                    fmtspec = fmtspec_match.group(0)
                    idx = fmtspec_match.end(0)
                    # We have a format spec, so find the end of the substitution
                    start = self._find_subst_end(string, idx)
                    default_string = string[idx : start - 1]

                    # Now decide what to do with the value and default_string
                    parts.append(
                        self._handle_expansion(
                            name, special, fmtspec, value, default_string
                        )
                    )
            else:
                varname_match = self._re_varname.match(string, idx + 1)
                if varname_match is None:
                    raise InvalidVariableNameError()

                value, special = self._get_variable(varname_match.group(0))
                if value is not None:
                    parts.append(value)
                elif self.strict:
                    raise KeyError(varname_match.group(0))

                idx = varname_match.end(0)
                start = idx

            idx = string.find("$", start)

        # If we have stuff 'at the end', add it to our parts
        if start < len(string):
            parts.append(string[start:])

        print(f"Parts: {parts}")
        return "".join(parts)

    def _handle_expansion(
        self, name: str, special: bool, spec: str, value: str | None, defstr: str
    ) -> str:
        match spec:
            case ":-":
                # Default value substitution
                if value is None or value == "":
                    return self.replace(defstr)
                return value
            case "-":
                # Default value substitution
                if value is None:
                    return self.replace(defstr)
                return value
            case ":=":
                # Assignment substitution
                if special:
                    raise SubstitutionError(
                        f"cannot use ':=' with special variable ${name}"
                    )
                if value is None or value == "":
                    value = self.replace(defstr)
                    self.values[name] = value
                return value
            case "=":
                # Assignment substitution
                if special:
                    raise SubstitutionError(
                        f"cannot use '=' with special variable ${name}"
                    )
                if value is None:
                    value = self.replace(defstr)
                    self.values[name] = value
                return value
            case ":?":
                # Error if missing substitution
                if value is None or value == "":
                    msg = self.replace(defstr)
                    if not self.raise_on_error_expansion:
                        if self.logger is None:
                            self.logger = logging.warning
                        self.logger(f"variable {name} not set or empty: {msg}")
                        return ""
                    raise SubstitutionError(f"variable {name} not set or empty: {msg}")
                return value
            case "?":
                # Error if missing subsitution
                if value is None:
                    msg = self.replace(defstr)
                    if not self.raise_on_error_expansion:
                        if self.logger is None:
                            self.logger = logging.warning
                        self.logger(f"variable {name} not set: {msg}")
                        return ""
                    raise SubstitutionError(f"variable {name} not set: {msg}")
                return value
            case ":+":
                # Alternate word substitution
                if value is None or value == "":
                    return ""
                return self.replace(defstr)
            case "+":
                # Alternate word substitution
                if value is None:
                    return ""
                return self.replace(defstr)
            case ":":
                # String slicing - not really posix, but generally available in shells
                # Check if the 'defstr' contains a ':', and if so split it
                idx = defstr.find(":")
                if idx < 0:
                    pos = self._variable_as_int(defstr)
                    if pos is None:
                        raise FormatStringError("non-numeric string slice offset")
                    if pos >= len(value):
                        return ""
                    return value[pos:]

                pos = self._variable_as_int(defstr[:idx])
                count = self._variable_as_int(defstr[idx + 1 :])
                if pos is None:
                    raise FormatStringError("non-numeric string slice offset")
                if count is None:
                    raise FormatStringError("non-numeric string slice length")
                return value[pos : pos + count]

    def _get_variable(self, name: str) -> tuple[str | None, bool]:
        # First, check if it is one of our special variables
        if name == "@" or name == "*":
            return " ".join(sys.argv[1:]), True
        if name == "#":
            return str(len(sys.argv)), True

        # Check if it is an integer; if so, return the arg corresponding to
        # that
        varidx = self._variable_as_int(name)
        if varidx is not None:
            # If we had a match
            if varidx < 0 or varidx >= len(sys.argv):
                return None, True
            # Otherwise, return the argument at that index
            return sys.argv[varidx], True

        # It's not an integer, so look it up in our values
        return self.values.get(name, None), False

    def _variable_as_int(self, name: str) -> int | None:
        try:
            return int(name)
        except ValueError:
            return None

    def _find_subst_end(self, string: str, start: int) -> int:
        next_delim = _re_search_delim.search(string, start)
        while next_delim is not None:
            # If the next delimiter found is '}', we're done - return the
            # character past the end
            if next_delim.group(0) == "}":
                return next_delim.end(0)

            idx = next_delim.start(0) + 1
            if idx >= len(string):
                raise UnterminatedFormatError()

            # Open bracket means recursively searching for the end
            if string[idx] == "{":
                varname_match = self._re_varname.match(string, idx + 1)
                if varname_match is None:
                    raise InvalidVariableNameError()

                idx = varname_match.end(0)
                fmtspec_match = self._re_format_spec.match(string, idx)
                if fmtspec_match is None:
                    if idx >= len(string) or string[idx] != "}":
                        raise UnterminatedFormatError()

                    start = idx + 1
                else:
                    start = self._find_subst_end(string, fmtspec_match.end(0))
            elif idx >= 2 and string[idx - 2] == "\\":
                # Skip escaped '$'
                start = idx + 1
            else:
                # non-bracket variable name; parse it and skip it
                varname_match = self._re_varname.match(string, idx)
                if varname_match is None:
                    raise InvalidVariableNameError()

                start = varname_match.end(0)

            next_delim = _re_search_delim.search(string, start)

        raise UnterminatedFormatError()  #
