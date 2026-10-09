"""Just enough S-expression handling for KiCad files: parse, query, write."""

import re


class Str(str):
    """A quoted string, written back with its quotes."""


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def parse(text):
    """KiCad S-expression text to nested lists of str / Str."""
    stack, out, pos = [], [], 0
    while True:
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1):
            stack.append(out)
            out = []
        elif m.group(2):
            done, out = out, stack.pop()
            out.append(done)
        elif m.group(3) is not None:
            out.append(Str(m.group(3).replace('\\"', '"').replace("\\\\", "\\")))
        else:
            out.append(m.group(4))
    return out[0] if len(out) == 1 else out


def dumps(node, indent=0):
    """Nested lists back to KiCad text."""
    if isinstance(node, Str):
        return '"' + node.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if not isinstance(node, list):
        return str(node)
    head = [dumps(x) for x in node if not isinstance(x, list)]
    kids = [x for x in node if isinstance(x, list)]
    text = "(" + " ".join(head)
    for k in kids:
        text += "\n" + "  " * (indent + 1) + dumps(k, indent + 1)
    return text + ")"


def children(node, name):
    """Direct sub-lists of `node` whose head is `name`."""
    return [x for x in node if isinstance(x, list) and x and x[0] == name]


def child(node, name):
    found = children(node, name)
    return found[0] if found else None
