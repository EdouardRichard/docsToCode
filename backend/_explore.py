import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

text = open('tests/fixtures/samples/openapi.json', encoding='utf-8').read()
lines = text.splitlines()
node = yaml.compose(text)

def find_key(mp, key):
    if not isinstance(mp, MappingNode):
        return None
    for k, v in mp.value:
        if isinstance(k, ScalarNode) and k.value == key:
            return v
    return None

root = node
paths = find_key(root, 'paths')
print('ROOT start', root.start_mark.line, 'end', root.end_mark.line, 'total', len(lines))
print('PATHS start', paths.start_mark.line, 'end', paths.end_mark.line)
users = find_key(paths, '/api/v1/users')
print('USERS start', users.start_mark.line, 'end', users.end_mark.line)
get_op = find_key(users, 'get')
print('GET start', get_op.start_mark.line, 'end', get_op.end_mark.line)
post_op = find_key(users, 'post')
print('POST start', post_op.start_mark.line, 'end', post_op.end_mark.line)
comps = find_key(root, 'components')
schemas = find_key(comps, 'schemas')
user_schema = find_key(schemas, 'User')
print('USER SCHEMA start', user_schema.start_mark.line, 'end', user_schema.end_mark.line)
role_schema = find_key(schemas, 'Role')
print('ROLE SCHEMA start', role_schema.start_mark.line, 'end', role_schema.end_mark.line)
for name, n in [('GET', get_op), ('POST', post_op), ('USER', user_schema)]:
    s = n.start_mark.line; e = n.end_mark.line
    print(f'--- {name} lines {s}..{e} (0-based) ---')
    for i in range(s, min(e + 2, len(lines))):
        print(f'{i+1}: {lines[i]}')
