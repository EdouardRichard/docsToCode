import tree_sitter_go as tsgo
from tree_sitter import Language, Parser

GO_LANGUAGE = Language(tsgo.language())
parser = Parser(GO_LANGUAGE)

with open("tests/fixtures/samples/service.go", "rb") as f:
    source = f.read()

tree = parser.parse(source)
root = tree.root_node


def walk(node, depth=0):
    print("  " * depth + f"{node.type}" +
          (f"  [{node.start_point[0]+1},{node.start_point[1]+1}-{node.end_point[0]+1},{node.end_point[1]+1}]") +
          (f"  text={node.text.decode()[:60]!r}" if node.child_count == 0 else ""))
    for c in node.children:
        walk(c, depth + 1)


print("=== ROOT:", root.type, "child_count:", root.child_count, "===")
walk(root)

print("\n\n=== has_error:", root.has_error, "===")

# Now malformed
print("\n\n############ MALFORMED ############")
with open("tests/fixtures/samples/malformed.go", "rb") as f:
    msource = f.read()
mtree = parser.parse(msource)
mroot = mtree.root_node
print("ROOT:", mroot.type, "child_count:", mroot.child_count, "has_error:", mroot.has_error)
walk(mroot)
