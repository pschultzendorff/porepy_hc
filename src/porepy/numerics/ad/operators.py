from enum import Enum
from typing import Optional, List, Any
from itertools import count

import numpy as np
import porepy as pp
import networkx as nx
import scipy.sparse as sps
import matplotlib.pyplot as plt

__all__ = [
    "Operator",
    "MergedOperator",
    "Matrix",
    "Array",
    "Scalar",
    "Variable",
    "MergedVariable",
    "Function",
    "Discretization",
]


Operation = Enum("Operation", ["void", "add", "sub", "mul", "evaluate", "div"])


class Tree:
    # https://stackoverflow.com/questions/2358045/how-can-i-implement-a-tree-in-python
    def __init__(self, operation: Operation, children: Optional[List["Tree"]] = None):

        self.op = operation

        self.children = []
        if children is not None:
            for child in children:
                self.add_child(child)

    def add_child(self, node):
        assert isinstance(node, Operator) or isinstance(node, pp.ad.Operator)
        self.children.append(node)


class Operator:
    def __init__(self, disc=None, name=None, grid=None, tree=None):
        if disc is not None:
            self._discr = disc
        if name is not None:
            self._name = name
            assert disc is not None
        if grid is not None:
            self.g = grid
        self._set_tree(tree)

    def _set_tree(self, tree=None):
        if tree is None:
            self.tree = Tree(Operation.void)
        else:
            self.tree = tree

    def is_leaf(self) -> bool:
        return len(self.tree.children) == 0

    def parse(self, gb) -> Any:
        """ Translate the operator into a numerical expression.

        Subclasses that represent atomic operators (leaves in a tree-representation of
        an operator) should override this method to retutrn e.g. a number, an array or a
        matrix.

        This method should not be called on operators that are formed as combinations
        of atomic operators; such operators should be evaluated by an Equation object.

        """
        raise NotImplementedError("This type of operator cannot be parsed right away")


    def __repr__(self) -> str:
        return f"Operator formed by {self._tree._op} with {len(self._tree._children)} children"

    def viz(self):
        G = nx.Graph()

        def parse_subgraph(node):
            G.add_node(node)
            if len(node.tree.children) == 0:
                return
            operation = node.tree.op
            G.add_node(operation)
            G.add_edge(node, operation)
            for child in node._tree._children:
                parse_subgraph(child)
                G.add_edge(child, operation)

        parse_subgraph(self)
        nx.draw(G, with_labels=True)
        plt.show()

    ### Below here are method for overloading aritmethic operators

    def __mul__(self, other):
        children = self._parse_other(other)
        tree = Tree(Operation.mul, children)
        return Operator(tree=tree)

    def __truediv__(self, other):
        children = self._parse_other(other)
        return Operator(tree=Tree(Operation.div, children))

    def __add__(self, other):
        children = self._parse_other(other)
        return Operator(tree=Tree(Operation.add, children))

    def __sub__(self, other):
        children = [self, other]
        return Operator(tree=Tree(Operation.sub, children))

    def __rmul__(self, other):
        return self.__mul__(other)

    def __radd__(self, other):
        return self.__add__(other)

    def __rsub__(self, other):
        return self.__sub__(other)

    def _parse_other(self, other):
        if isinstance(other, float) or isinstance(other, int):
            return [self, pp.ad.Scalar(other)]
        elif isinstance(other, np.ndarray):
            return [self, pp.ad.Array(other)]
        elif isinstance(other, sps.spmatrix):
            return [self, pp.ad.Matrix(other)]
        elif isinstance(other, pp.ad.Operator) or isinstance(other, Operator):
            return [self, other]
        else:
            raise ValueError(f"Cannot parse {other} as an AD operator")


class MergedOperator(Operator):
    # This will likely be converted to operator, that is, non-merged operators are removed
    def __init__(self, grid_discr, key, mat_dict_key: str = None):
        self.grid_discr = grid_discr
        self.key = key

        # Special field to access matrix dictionary for Biot
        self.mat_dict_key = mat_dict_key

        self._set_tree(None)

    def __repr__(self) -> str:
        return f"Operator with key {self.key} defined on {len(self.grid_discr)} grids"

    def parse(self, gb):
        mat = []
        for g, discr in self.grid_discr.items():
            if isinstance(g, pp.Grid):
                data = gb.node_props(g)
            else:
                data = gb.edge_props(g)
            if self.mat_dict_key is not None:
                mat_dict_key = self.mat_dict_key
            else:
                mat_dict_key = discr.keyword

            mat_dict = data[pp.DISCRETIZATION_MATRICES][mat_dict_key]

            # Get the submatrix for the right discretization
            key = self.key
            mat_key = getattr(discr, key + "_matrix_key")
            mat.append(mat_dict[mat_key])

        return sps.block_diag(mat)

class Matrix(Operator):
    def __init__(self, mat):
        self.mat = mat
        self._set_tree()
        self.shape = self.mat.shape

    def __repr__(self) -> str:
        return f"Matrix with shape {self.mat.shape} and {self.mat.data.size} elements"

    def parse(self, gb):
        return self.mat

class Array(Operator):
    def __init__(self, values):
        self.values = values
        self._set_tree()

    def __repr__(self) -> str:
        return f"Wrapped numpy array of size {self.values.size}"


    def parse(self, gb):
        return self.values

class Scalar(Operator):
    def __init__(self, value):
        self.value = value
        self._set_tree()

    def __repr__(self) -> str:
        return f"Wrapped scalar with value {self.value}"

    def parse(self):
        return self.value

class Variable(Operator):

    _ids = count(0)

    def __init__(self, name, ndof, grid_like, num_cells: int = 0):
        self._name = name
        self._cells = ndof.get("cells", 0)
        self._faces = ndof.get("faces", 0)
        self._nodes = ndof.get("nodes", 0)
        self.g = grid_like

        # The number of cells in the grid. Will only be used if grid_like is a tuple
        # that is, if this is a mortar variable
        self._num_cells = num_cells

        self.id = next(self._ids)

        self._set_tree()

    def size(self) -> int:
        if isinstance(self.g, tuple):
            # This is a mortar grid. Assume that there are only cell unknowns
            return self._num_cells * self._cells
        else:
            return (
                self.g.num_cells * self._cells
                + self.g.num_faces * self._faces
                + self.g.num_nodes * self._nodes
            )

    def __repr__(self) -> str:
        s = (
            f"Variable {self._name}, id: {self.id}\n"
            f"Degrees of freedom in cells: {self._cells}, faces: {self._faces}, "
            f"nodes: {self._nodes}\n"
        )
        return s


class MergedVariable(Variable):
    # TODO: Is it okay to generate the same variable (grid, name) many times?
    # The whole concept needs a massive cleanup
    def __init__(self, variables):
        self.sub_vars = variables
        self.id = next(self._ids)
        self._name = variables[0]._name
        self._set_tree()

        self.is_interface = isinstance(self.sub_vars[0].g, tuple)

        all_names = set(var._name for var in variables)
        assert len(all_names) == 1

    def __repr__(self) -> str:
        sz = np.sum([var.size() for var in self.sub_vars])
        if self.is_interface:
            s = "Merged interface"
        else:
            s = "Merged"

        s += (
            f" variable with name {self._name}, id {self.id}\n"
            f"Composed of {len(self.sub_vars)} variables\n"
            f"Degrees of freedom in cells: {self.sub_vars[0]._cells}"
            f", faces: {self.sub_vars[0]._faces}, nodes: {self.sub_vars[0]._nodes}\n"
            f"Total size: {sz}\n"
        )

        return s


class Function(Operator):
    def __init__(self, func, name):
        self.func = func
        self.name = name
        self._set_tree()

    def __mul__(self, other):
        raise RuntimeError("Functions should only be evaluated")

    def __add__(self, other):
        raise RuntimeError("Functions should only be evaluated")

    def __sub__(self, other):
        raise RuntimeError("Functions should only be evaluated")

    def __call__(self, *args):
        children = [self, *args]
        op = Operator(tree=Tree(Operation.evaluate, children=children))
        return op

    def __repr__(self) -> str:
        s = f"AD function with name {self.name}"

        return s

    def parse(self, gb):
        return self

class Discretization:
    def __init__(self, grid_discr, name=None, tree=None, mat_dict_key: str = None):

        self.grid_discr = grid_discr
        key_set = []
        self.mat_dict_key = mat_dict_key
        if name is None:
            names = []
            for discr in grid_discr.values():
                names.append(discr.__class__.__name__)

            self.name = "_".join(list(set(names)))
        else:
            self.name = name

        for i, discr in enumerate(grid_discr.values()):
            for s in dir(discr):
                if s.endswith("_matrix_key"):
                    if i == 0:
                        key = s[:-11]
                        key_set.append(key)
                    else:
                        if key not in key_set:
                            raise ValueError(
                                "Merged disrcetization should have uniform set of operations"
                            )

        for key in key_set:
            op = MergedOperator(grid_discr, key, self.mat_dict_key)
            setattr(self, key, op)

    def __repr__(self) -> str:

        discr_counter = {}

        for discr in self.grid_discr.values():
            if discr not in discr_counter:
                discr_counter[discr] = 0
            discr_counter[discr] += 1

        s = f"Merged discretization with name {self.name}. Sub discretizations:\n"
        for key, val in discr_counter.items():
            s += f"{val} occurences of discretization {key}"

        return s
