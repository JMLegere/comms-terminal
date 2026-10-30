"""Writers replay the same way everywhere (ADR 0012, NFR-4): each takes `now`, and nothing it reaches reads the clock
except as `now or clock.now()` with `now` handed down, so the laptop and the service apply a queued write at the
moment the console stamped on it."""

import ast
import inspect

from comms_terminal import files, inbox, pipeline, sequences, writers

MODULES = {"inbox": inbox, "sequences": sequences, "pipeline": pipeline, "files": files}


def functions():
    out = {}
    for name, module in MODULES.items():
        for node in ast.parse(inspect.getsource(module)).body:
            if isinstance(node, ast.FunctionDef):
                out[f"{name}.{node.name}"] = (name, node)
    return out


def params(node):
    a = node.args
    return [p.arg for p in a.posonlyargs + a.args + a.kwonlyargs]


def callee(module, call, funcs):
    f = call.func
    if isinstance(f, ast.Name) and f"{module}.{f.id}" in funcs:
        return f"{module}.{f.id}"
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f"{f.value.id}.{f.attr}" in funcs:
        return f"{f.value.id}.{f.attr}"
    return None


def reachable(funcs):
    seen, todo = set(), list(writers.WRITERS)
    while todo:
        name = todo.pop()
        if name in seen or name not in funcs:
            continue
        seen.add(name)
        module, node = funcs[name]
        todo += [c for call in ast.walk(node) if isinstance(call, ast.Call) and (c := callee(module, call, funcs))]
    return seen


def violations():
    funcs = functions()
    out = [f"{w}: no now parameter" for w in sorted(writers.WRITERS) if "now" not in params(funcs[w][1])]
    for name in sorted(reachable(funcs)):
        module, node = funcs[name]
        parents = {c: p for p in ast.walk(node) for c in ast.iter_child_nodes(p)}
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            f = call.func
            if isinstance(f, ast.Attribute) and f.attr == "now" and isinstance(f.value, ast.Name) and f.value.id == "clock":
                p = parents.get(call)
                guarded = (isinstance(p, ast.BoolOp) and isinstance(p.op, ast.Or) and p.values[-1] is call
                           and isinstance(p.values[0], ast.Name) and p.values[0].id == "now")
                if not guarded or "now" not in params(node):
                    out.append(f"{name}:{call.lineno}: reads the clock")
            elif (c := callee(module, call, funcs)) and "now" in params(funcs[c][1]) and "now" in params(node):
                passed = any(k.arg == "now" for k in call.keywords) or any(
                    isinstance(a, ast.Name) and a.id == "now" for a in call.args)
                if not passed:
                    out.append(f"{name}:{call.lineno}: calls {c} without now")
    return out


def test_every_writer_takes_now_and_hands_it_down():
    assert violations() == []
