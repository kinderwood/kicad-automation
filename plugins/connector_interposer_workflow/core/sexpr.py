"""Small strict KiCad s-expression reader, including quoted escapes/comments."""
import re
import json

class Atom(str):
    pass

def dumps(node):
    if isinstance(node, list):
        return "(" + " ".join(dumps(v) for v in node) + ")"
    return str(node) if isinstance(node, Atom) else json.dumps(str(node), ensure_ascii=False)


def loads(text):
    tokens, position = [], 0
    while position < len(text):
        char = text[position]
        if char.isspace():
            position += 1
        elif char == ';':
            end = text.find('\n', position)
            position = len(text) if end < 0 else end+1
        elif char in '()':
            tokens.append(char)
            position += 1
        elif char == '"':
            position += 1
            value = []
            while position < len(text):
                char = text[position]
                position += 1
                if char == '"':
                    break
                if char == '\\':
                    if position >= len(text):
                        raise ValueError("Unterminated string escape")
                    char = text[position]
                    position += 1
                    char = {'n':'\n', 'r':'\r', 't':'\t'}.get(char, char)
                value.append(char)
            else:
                raise ValueError("Unterminated quoted string")
            tokens.append(('string', ''.join(value)))
        else:
            match = re.match(r'[^\s();"]+', text[position:])
            if not match:
                raise ValueError("Invalid s-expression token")
            tokens.append(('atom', match[0]))
            position += len(match[0])
    stack, root = [], None
    for token in tokens:
        if token == '(':
            node = []
            if stack:
                stack[-1].append(node)
            elif root is not None:
                raise ValueError("Multiple root expressions")
            else:
                root = node
            stack.append(node)
        elif token == ')':
            if not stack:
                raise ValueError("Unexpected closing parenthesis")
            stack.pop()
        elif not stack:
            raise ValueError("Token outside root expression")
        else:
            stack[-1].append(Atom(token[1]) if token[0] == "atom" else token[1])
    if stack or root is None:
        raise ValueError("Incomplete s-expression")
    return root


def children(node, name):
    return [child for child in node if isinstance(child, list) and child and child[0] == name]


def child(node, name, default=None):
    return next(iter(children(node, name)), default)
