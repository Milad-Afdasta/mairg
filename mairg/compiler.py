"""Validate whole programs and compile each live DAG to straight-line Python AST."""
from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
from typing import Any

from .language import (
    HOSTS,
    MAX_SOURCE,
    OPS,
    TYPES,
    AppError,
    decode_json,
    encode_json,
    json_copy,
)
from .runtime import CompiledProject, project_from_generated


def _error(message: str, function: int | None = None, node: int | None = None) -> None:
    raise AppError('invalid_program', message, function=function, node=node)


def _exact(value: Any, keys: set[str], label: str) -> None:
    if type(value) is not dict or set(value) != keys:
        _error(f'{label} requires exactly {sorted(keys)}')


def _identity(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 2**31 - 1


def project_path(root: Path, name: Any) -> Path:
    if type(name) is not str or not name or '\\' in name or '\x00' in name:
        _error('Project paths must be canonical relative paths')
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or str(path) != name or any(part in ('..', '.') for part in path.parts):
        _error('Project paths must be canonical relative paths')
    resolved = (root / name).resolve()
    if not resolved.is_relative_to(root.resolve()):
        _error('Project path escapes root')
    return resolved


def _manifest(raw: Any, root: Path, assets: Any) -> dict[str, Any]:
    _exact(raw, {'format', 'name', 'program', 'bindings', 'database', 'assets', 'routes', 'startup', 'tests'}, 'Manifest')
    if raw['format'] != 'mairg-project-1':
        _error('Unknown project format')
    if type(raw['name']) is not str or not raw['name']:
        _error('Project name must be nonempty text')
    project_path(root, raw['program'])
    if type(raw['bindings']) is not dict or any(type(k) is not str or not k or type(v) is not str or v not in HOSTS
                                                for k, v in raw['bindings'].items()):
        _error('Invalid host bindings')
    if raw['database'] is not None:
        # State paths have the same canonical syntax, with a separate resource owner.
        project_path(Path('/'), raw['database'])
    paths: set[Path] = {project_path(root, raw['program'])}
    for field in ('assets', 'tests'):
        if type(raw[field]) is not list:
            _error(f'{field} must be a list')
        for name in raw[field]:
            path = project_path(root, name)
            if path in paths:
                _error('Duplicate canonical project path')
            paths.add(path)
    if type(assets) is not dict or set(assets) != set(raw['assets']):
        _error('Asset snapshots must exactly match declared assets')
    if any(type(value) is not str for value in assets.values()):
        _error('Asset snapshots must be UTF-8 text')
    if type(raw['routes']) is not list:
        _error('Routes must be a list')
    for route in raw['routes']:
        _exact(route, {'method', 'path', 'export'}, 'Route')
        if any(type(route[key]) is not str for key in route):
            _error('Route fields must be text')
    if raw['startup'] is not None and type(raw['startup']) is not str:
        _error('Startup must be an export name or null')
    return raw


def _validate(document: dict[str, Any], manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    _exact(document, {'format', 'functions', 'exports'}, 'Program')
    if document['format'] != 'mairg-app-1':
        _error('Unknown program format; expected mairg-app-1')
    functions = document['functions']
    if type(functions) is not list or not 0 <= len(functions) <= 128:
        _error('Program permits at most 128 functions')
    infos: dict[int, dict[str, Any]] = {}
    for function in functions:
        _exact(function, {'id', 'params', 'result', 'nodes', 'return'}, 'Function')
        fid = function['id']
        if not _identity(fid) or fid in infos:
            _error('Invalid or duplicate function ID')
        if type(function['params']) is not list or len(function['params']) > 64 or any(
                type(atom) is not str or atom not in TYPES for atom in function['params']):
            _error('Invalid parameter type list', fid)
        if type(function['result']) is not str or function['result'] not in TYPES:
            _error('Invalid result type', fid)
        if type(function['nodes']) is not list or not 1 <= len(function['nodes']) <= 1024:
            _error('Function requires 1..1024 nodes', fid)
        nodes: dict[int, dict[str, Any]] = {}
        for node in function['nodes']:
            if type(node) is not dict or not {'id', 'op', 'args'} <= set(node):
                _error('Malformed node', fid)
            nid, op = node['id'], node['op']
            if not _identity(nid) or nid in nodes:
                _error('Invalid or duplicate node ID', fid)
            if type(op) is not str or op not in OPS:
                _error('Unknown operation', fid, nid)
            spec = OPS[op]
            required = {'id', 'op', 'args', *spec['fields']}
            if not required <= set(node) or set(node) - required - {'after'}:
                _error('Unexpected or missing node fields', fid, nid)
            args, after = node['args'], node.get('after', [])
            if type(args) is not list or not spec['min_args'] <= len(args) <= spec['max_args']:
                _error('Invalid operation arity', fid, nid)
            if type(after) is not list or any(not _identity(ref) for ref in args + after):
                _error('Dependencies must be local node IDs', fid, nid)
            if len(after) != len(set(after)):
                _error('Duplicate after dependency', fid, nid)
            if op == 'param' and (type(node['index']) is not int or not 0 <= node['index'] < len(function['params'])):
                _error('Invalid parameter index', fid, nid)
            if op == 'record' and (type(node['keys']) is not list or len(node['keys']) != len(args)
                    or any(type(key) is not str for key in node['keys'])
                    or len(set(node['keys'])) != len(node['keys'])):
                _error('Record keys must be distinct strings matching args', fid, nid)
            if op == 'host' and (type(node['binding']) is not str or node['binding'] not in manifest['bindings']):
                _error('Host binding is not declared', fid, nid)
            for field in ('target', 'then', 'else'):
                if field in node and not _identity(node[field]):
                    _error('Invalid target function ID', fid, nid)
            nodes[nid] = node
        if not _identity(function['return']) or function['return'] not in nodes:
            _error('Return must refer to a local node', fid)
        order: list[int] = []
        ancestors: dict[int, set[int]] = {}
        heights: dict[int, int] = {}
        visiting: set[int] = set()

        def visit(nid: int, depth: int = 0) -> None:
            if depth > 256:
                _error('Dependency depth exceeds limit', fid, nid)
            if nid in visiting:
                _error('Node graph contains a cycle', fid, nid)
            if nid in ancestors:
                return
            if nid not in nodes:
                _error('Unknown local node reference', fid, nid)
            visiting.add(nid)
            previous: set[int] = set()
            for dep in nodes[nid]['args'] + nodes[nid].get('after', []):
                visit(dep, depth + 1)
                previous.add(dep)
                previous.update(ancestors[dep])
            visiting.remove(nid)
            ancestors[nid] = previous
            heights[nid] = 1 + max((heights[d] for d in nodes[nid]['args'] + nodes[nid].get('after', [])), default=0)
            if heights[nid] > 256:
                _error('Dependency depth exceeds limit', fid, nid)
            order.append(nid)
        for nid in nodes:
            visit(nid)
        live = ancestors[function['return']] | {function['return']}
        infos[fid] = {'params': function['params'], 'result': function['result'], 'nodes': nodes,
                      'order': [nid for nid in order if nid in live], 'return': function['return'],
                      'ancestors': ancestors, 'effects': set(), 'transaction': False}
    exports = document['exports']
    if type(exports) is not dict or any(type(k) is not str or not k
            or not _identity(v) or v not in infos for k, v in exports.items()):
        _error('Exports must map nonempty names to defined function IDs')
    for route in manifest['routes']:
        if route['export'] not in exports or len(infos[exports[route['export']]]['params']) != 1:
            _error('Route exports must accept one argument')
    startup = manifest['startup']
    if startup is not None and (startup not in exports or infos[exports[startup]]['params']):
        _error('Startup export must accept zero arguments')

    def targets(node: dict[str, Any]) -> list[int]:
        return [node[field] for field in ('target', 'then', 'else') if field in node]

    for fid, info in infos.items():
        for nid, node in info['nodes'].items():
            op = node['op']
            for target in targets(node):
                if target not in infos:
                    _error('Unknown target function', fid, nid)
                arity = len(node['args']) - (1 if op == 'choose' else 0)
                if op in ('map', 'filter'):
                    arity = 1
                elif op == 'fold':
                    arity = 2
                if len(infos[target]['params']) != arity:
                    _error('Target function arity mismatch', fid, nid)
            if op == 'host':
                builtin = manifest['bindings'][node['binding']]
                if len(node['args']) != len(HOSTS[builtin]['params']):
                    _error('Host arity mismatch', fid, nid)

    def effects(node: dict[str, Any]) -> set[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        if node['op'] == 'host':
            result.update(tuple(pair) for pair in HOSTS[manifest['bindings'][node['binding']]]['effects'])
        if node['op'] == 'transaction':
            result.add(('db', 'write'))
        for target in targets(node):
            result.update(infos[target]['effects'])
        return result

    changed = True
    while changed:
        changed = False
        for info in infos.values():
            found: set[tuple[str, str]] = set()
            tx = False
            for node in info['nodes'].values():
                found.update(effects(node))
                tx |= node['op'] == 'transaction' or any(infos[t]['transaction'] for t in targets(node))
            if found != info['effects'] or tx != info['transaction']:
                info['effects'], info['transaction'] = found, tx
                changed = True
    for fid, info in infos.items():
        for nid, node in info['nodes'].items():
            if node['op'] in ('map', 'filter') and infos[node['target']]['effects']:
                _error('Map/filter callbacks must be transitively pure', fid, nid)
            if node['op'] == 'transaction':
                target_info = infos[node['target']]
                if target_info['transaction'] or any(domain != 'db' for domain, _ in target_info['effects']):
                    _error('Transactions require DB-only target without nested transactions', fid, nid)
            if effects(node) and nid not in info['order']:
                _error('Effectful node is not reachable from return', fid, nid)
        live_effects = [(nid, effects(info['nodes'][nid])) for nid in info['order']]
        for index, (a, ae) in enumerate(live_effects):
            for b, be in live_effects[index + 1:]:
                conflict = any(ad == bd and ('write' in (am, bm)) for ad, am in ae for bd, bm in be)
                if conflict and a not in info['ancestors'][b] and b not in info['ancestors'][a]:
                    _error('Conflicting effects require a dependency path', fid, b)
    return infos


def compile_documents(manifest: Any, document: Any, *, root: str | Path,
                      assets: dict[str, str]) -> CompiledProject:
    root = Path(root).resolve()
    manifest, document = json_copy(manifest), json_copy(document)
    if type(assets) is not dict:
        _error('Assets must be a snapshot record')
    assets = {key: json_copy(value) for key, value in assets.items()}
    if len(encode_json(document).encode('utf-8')) > MAX_SOURCE:
        raise AppError('resource_limit', 'Source exceeds byte limit')
    manifest = _manifest(manifest, root, assets)
    infos = _validate(document, manifest)
    from .web import validate_routes
    validate_routes(manifest)
    from .project import source_revision
    revision = source_revision(manifest, document, assets)
    metadata = {'manifest': manifest, 'document': document, 'assets': assets, 'source_revision': revision,
                'functions': {str(fid): {'params': info['params'], 'result': info['result'],
                                        'effects': [list(effect) for effect in sorted(info['effects'])], 'transaction': info['transaction']}
                              for fid, info in infos.items()}}
    tree = ast.Module(body=[], type_ignores=[])
    tree.body.append(ast.ImportFrom(module='mairg.runtime', names=[ast.alias(name='project_from_generated')], level=0))
    tree.body.append(ast.ImportFrom(module='mairg.language', names=[ast.alias(name='decode_json')], level=0))
    # repr produces only Python literals; ast.parse here parses compiler-owned syntax and literals.
    tree.body.append(ast.Assign(targets=[ast.Name(id='METADATA', ctx=ast.Store())], value=ast.Call(func=ast.Name(id='decode_json', ctx=ast.Load()), args=[ast.Constant(encode_json(metadata, max_bytes=None))], keywords=[ast.keyword(arg='max_bytes', value=ast.Constant(len(encode_json(metadata, max_bytes=None).encode('utf-8')) + 1024))])))
    for fid, info in infos.items():
        body: list[ast.stmt] = []
        for nid in info['order']:
            node = info['nodes'][nid]
            extra = {k: v for k, v in node.items() if k not in ('id', 'op', 'args', 'after')}
            extra_name = f'_e_{fid}_{nid}'
            tree.body.append(ast.Assign(targets=[ast.Name(id=extra_name, ctx=ast.Store())], value=ast.Call(
                func=ast.Name(id='decode_json', ctx=ast.Load()), args=[ast.Constant(encode_json(extra))], keywords=[])))
            call = ast.Call(func=ast.Attribute(value=ast.Name(id='ctx', ctx=ast.Load()), attr='evaluate', ctx=ast.Load()),
                            args=[ast.Constant(fid), ast.Constant(nid), ast.Constant(node['op']),
                                  ast.List(elts=[ast.Name(id=f'n_{ref}', ctx=ast.Load()) for ref in node['args']], ctx=ast.Load()),
                                  ast.Name(id=extra_name, ctx=ast.Load()), ast.Name(id='p', ctx=ast.Load())], keywords=[])
            body.append(ast.Assign(targets=[ast.Name(id=f'n_{nid}', ctx=ast.Store())], value=call))
        body.append(ast.Return(value=ast.Name(id=f'n_{info["return"]}', ctx=ast.Load())))
        tree.body.append(ast.FunctionDef(name=f'_f_{fid}', args=ast.arguments(posonlyargs=[], args=[ast.arg(arg='ctx'), ast.arg(arg='p')],
                         kwonlyargs=[], kw_defaults=[], defaults=[]), body=body, decorator_list=[]))
    tree.body.append(ast.Assign(targets=[ast.Name(id='FUNCTIONS', ctx=ast.Store())], value=ast.Dict(
        keys=[ast.Constant(fid) for fid in infos], values=[ast.Name(id=f'_f_{fid}', ctx=ast.Load()) for fid in infos])))
    tree.body.extend(ast.parse('def load_project(root=None):\n    return project_from_generated(METADATA, FUNCTIONS, root=root)\n').body)
    ast.fix_missing_locations(tree)
    source = ast.unparse(tree) + '\n'
    namespace: dict[str, Any] = {}
    exec(compile(tree, '<mairg-generated>', 'exec'), namespace)
    project = project_from_generated(metadata, namespace['FUNCTIONS'], root=root)
    project.generated_source = source
    return project


def compile_project(root: str | Path) -> CompiledProject:
    root = Path(root).resolve()
    try:
        manifest = decode_json((root / 'mairg.json').read_bytes(), max_bytes=MAX_SOURCE)
        if type(manifest) is not dict:
            _error('Manifest must be an object')
        program = project_path(root, manifest.get('program'))
        document = decode_json(program.read_bytes(), max_bytes=MAX_SOURCE)
        names = manifest.get('assets', [])
        if type(names) is not list:
            _error('Assets must be a list')
        assets: dict[str, str] = {}
        for name in names:
            path = project_path(root, name)
            if path.stat().st_size > 16_777_216:
                raise AppError('resource_limit', 'Asset exceeds byte limit')
            assets[name] = path.read_text(encoding='utf-8')
        return compile_documents(manifest, document, root=root, assets=assets)
    except AppError:
        raise
    except (OSError, UnicodeError) as exc:
        raise AppError('project_error', str(exc)) from exc
