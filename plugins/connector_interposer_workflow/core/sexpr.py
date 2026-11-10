"""Small strict KiCad s-expression reader, including quoted escapes/comments."""
import re
import json

class Atom(str):
    pass

def dumps(node):
    if isinstance(node, list):
        return "(" + " ".join(dumps(v) for v in node) + ")"
    return str(node) if isinstance(node, Atom) else json.dumps(str(node), ensure_ascii=False)


_SKIP = re.compile(r'(?:\s+|;[^\n]*)+')
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"', re.DOTALL)
_DANGLING_ESCAPE = re.compile(r'"(?:[^"\\]|\\.)*\\\Z', re.DOTALL)
_ATOM = re.compile(r'[^\s();"]+')
_ESCAPE = re.compile(r'\\(.)', re.DOTALL)
_ESCAPES = {'n': '\n', 'r': '\r', 't': '\t'}


def _unescape(match):
    return _ESCAPES.get(match[1], match[1])


def loads(text):
    """Parse one root expression in a single pass over the text.

    Every pattern is matched in place. Slicing the remaining text per token
    made large libraries quadratically expensive to read.
    """
    stack, root = [], None
    position, length = 0, len(text)
    skip, string, atom = _SKIP.match, _STRING.match, _ATOM.match
    while position < length:
        match = skip(text, position)
        if match:
            position = match.end()
            if position >= length:
                break
        char = text[position]
        if char == '(':
            node = []
            if stack:
                stack[-1].append(node)
            elif root is not None:
                raise ValueError("Multiple root expressions")
            else:
                root = node
            stack.append(node)
            position += 1
            continue
        if char == ')':
            if not stack:
                raise ValueError("Unexpected closing parenthesis")
            stack.pop()
            position += 1
            continue
        if char == '"':
            match = string(text, position)
            if not match:
                if _DANGLING_ESCAPE.match(text, position):
                    raise ValueError("Unterminated string escape")
                raise ValueError("Unterminated quoted string")
            value = match[1]
            if '\\' in value:
                value = _ESCAPE.sub(_unescape, value)
        else:
            match = atom(text, position)
            if not match:
                raise ValueError("Invalid s-expression token")
            value = Atom(match[0])
        if not stack:
            raise ValueError("Token outside root expression")
        stack[-1].append(value)
        position = match.end()
    if stack or root is None:
        raise ValueError("Incomplete s-expression")
    return root


def children(node, name):
    return [child for child in node if isinstance(child, list) and child and child[0] == name]


def child(node, name, default=None):
    return next(iter(children(node, name)), default)
