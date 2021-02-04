import ast
import logging
from functools import lru_cache
from typing import Any, Dict, List, Tuple
from uuid import UUID

from django.utils.dateparse import (
    parse_date,
    parse_datetime,
    parse_duration,
    parse_time,
)


logger = logging.getLogger(__name__)

# Lambdas that attempt to convert something that failed while being parsed by `ast.literal_eval`.
CASTERS = [
    lambda a: parse_datetime(a),
    lambda a: parse_time(a),
    lambda a: parse_date(a),
    lambda a: parse_duration(a),
    lambda a: UUID(a),
]


class InvalidKwarg(Exception):
    pass


def _get_expr_string(expr: ast.expr) -> str:
    """
    Builds a string based on traversing `ast.Attribute` and `ast.Name` expressions.

    Args:
        expr: Expression node of the the AST tree. Only handles `ast.Attribute` and `ast.Name` expressions.

    Returns:
        String based on the expression nodes.
    """

    current_expr = expr
    expr_str = ""

    while current_expr:
        if isinstance(current_expr, ast.Name):
            if not expr_str:
                expr_str = current_expr.id
            else:
                expr_str = f"{current_expr.id}.{expr_str}"

            break
        elif isinstance(current_expr, ast.Attribute):
            if not expr_str:
                expr_str = current_expr.attr
            else:
                expr_str = f"{current_expr.attr}.{expr_str}"

            current_expr = current_expr.value
        else:
            break

    return expr_str


@lru_cache(maxsize=128)
def eval_value(value):
    """
    Uses `ast.literal_eval` to parse strings into an appropriate Python primative.

    Also returns an appropriate object for strings that look like they represent datetime, 
    date, time, duration, or UUID.
    """

    try:
        value = ast.literal_eval(value)
    except SyntaxError:
        for caster in CASTERS:
            try:
                casted_value = caster(value)

                if casted_value:
                    value = casted_value
                    break
            except ValueError:
                pass

    return value


@lru_cache(maxsize=128)
def parse_kwarg(kwarg: str, raise_if_unparseable=False) -> Dict[str, Any]:
    """
    Parses a potential kwarg as a string into a dictionary.
    
    Example:
        `parse_kwarg("test='1'")` == `{"test": "1"}`

    Args:
        kwarg: Potential kwarg as a string. e.g. "test='1'".
        raise_if_unparseable: Raise an error if the `kwarg` cannot be parsed. Defaults to `False`.

    Returns:
        Dictionary of key-value pairs.
    """

    try:
        tree = ast.parse(kwarg, "eval")

        if tree.body and isinstance(tree.body[0], ast.Assign):
            assign = tree.body[0]

            try:
                target = assign.targets[0]
                key = _get_expr_string(target)

                return {key: eval_value(assign.value)}
            except ValueError:
                if raise_if_unparseable:
                    raise

                # The value can be a template variable that will get set from the context when
                # the templatetag is rendered, so just return the expr as a string.
                value = _get_expr_string(assign.value)
                return {target.id: value}
        else:
            raise InvalidKwarg(f"'{kwarg}' is invalid")
    except SyntaxError:
        raise InvalidKwarg(f"'{kwarg}' could not be parsed")


@lru_cache(maxsize=128)
def parse_call_method_name(call_method_name: str) -> Tuple[str, List[Any]]:
    """
    Parses the method name from the request payload into a set of parameters to pass to a method.

    Args:
        param call_method_name: String representation of a method name with parameters, e.g. "set_name('Bob')"

    Returns:
        Tuple of method_name and a list of arguments.
    """

    dollar_func = False

    # Deal with special methods that start with a "$"
    if call_method_name.startswith("$"):
        dollar_func = True
        call_method_name = call_method_name[1:]

    tree = ast.parse(call_method_name, "eval")
    method_name = call_method_name
    args: List[Any] = []
    kwargs: Dict[str, Any] = {}

    if tree.body and isinstance(tree.body[0].value, ast.Call):
        call = tree.body[0].value
        method_name = call.func.id
        args = [eval_value(arg) for arg in call.args]

        # Not returned, but might be usable
        kwargs = {kw.arg: eval_value(kw.value) for kw in call.keywords}

    # Add "$" back to special functions
    if dollar_func:
        method_name = f"${method_name}"

    return (method_name, args, kwargs)
