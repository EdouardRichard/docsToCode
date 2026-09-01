import yaml
from yaml.nodes import MappingNode, ScalarNode

text = open('tests/fixtures/samples/openapi.yaml', encoding='utf-8').read()
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
print('total', len(lines))
paths = find_key(root, 'paths')
prods = find_key(paths, '/api/v1/products')
get_op = find_key(prods, 'get')
post_op = find_key(prods, 'post')
print('GET start', get_op.start_mark.line, 'end', get_op.end_mark.line)
print('POST start', post_op.start_mark.line, 'end', post_op.end_mark.line)
comps = find_key(root, 'components')
schemas = find_key(comps, 'schemas')
prod_s = find_key(schemas, 'Product')
cat_s = find_key(schemas, 'Category')
print('PRODUCT start', prod_s.start_mark.line, 'end', prod_s.end_mark.line)
print('CATEGORY start', cat_s.start_mark.line, 'end', cat_s.end_mark.line)
for name, n in [('GET', get_op), ('POST', post_op), ('PRODUCT', prod_s), ('CATEGORY', cat_s)]:
    s = n.start_mark.line; e = n.end_mark.line
    print(f'--- {name} 0-based {s}..{e} => 1-based {s+1}..{e+1} ---')
    for i in range(s, min(e + 2, len(lines))):
        print(f'{i+1}: {lines[i]}')
