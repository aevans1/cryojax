"""
Utilities for creating equinox filtered transformations. These routines
are modified from `zodiax`, which was created for the project dLux: https://louisdesdoigts.github.io/dLux/.
"""

import equinox as eqx
from jaxtyping import PyTree
from functools import wraps, partial
from typing import Callable, Union, Any, Optional, Hashable


def filter_grad(
    func: Callable,
    filter_spec: PyTree[Union[bool, Callable[[Any], bool]]],
    is_leaf: Optional[Callable[[Any], bool]] = None,
    *,
    has_aux: bool = False,
) -> Callable:

    @wraps(func)
    def partition_and_recombine_fn(pytree: PyTree, *args: Any, **kwargs: Any):

        @partial(
            eqx.filter_grad,
            has_aux=has_aux,
        )
        def recombine_fn(
            pytree_if_true: PyTree, pytree_if_false: PyTree, *args: Any, **kwargs: Any
        ):
            pytree = eqx.combine(pytree_if_true, pytree_if_false, is_leaf=is_leaf)
            return func(pytree, *args, **kwargs)

        pytree_if_true, pytree_if_false = eqx.partition(
            pytree, filter_spec, is_leaf=is_leaf
        )
        return recombine_fn(pytree_if_true, pytree_if_false, *args, **kwargs)

    return partition_and_recombine_fn


def filter_value_and_grad(
    func: Callable,
    filter_spec: PyTree[Union[bool, Callable[[Any], bool]]],
    is_leaf: Optional[Callable[[Any], bool]] = None,
    *,
    has_aux: bool = False,
) -> Callable:

    @wraps(func)
    def partition_and_recombine_fn(pytree: PyTree, *args: Any, **kwargs: Any):

        @partial(
            eqx.filter_value_and_grad,
            has_aux=has_aux,
        )
        def recombine_fn(
            pytree_if_true: PyTree, pytree_if_false: PyTree, *args: Any, **kwargs: Any
        ):
            pytree = eqx.combine(pytree_if_true, pytree_if_false, is_leaf=is_leaf)
            return func(pytree, *args, **kwargs)

        pytree_if_true, pytree_if_false = eqx.partition(
            pytree, filter_spec, is_leaf=is_leaf
        )
        return recombine_fn(pytree_if_true, pytree_if_false, *args, **kwargs)

    return partition_and_recombine_fn


def filter_vmap(
    func: Callable,
    filter_spec: PyTree[Union[bool, Callable[[Any], bool]]],
    is_leaf: Optional[Callable[[Any], bool]] = None,
    *,
    in_axes: PyTree[Union[int, None, Callable[[Any], Optional[int]]]] = eqx.if_array(
        axis=0
    ),
    out_axes: PyTree[Union[int, None, Callable[[Any], Optional[int]]]] = eqx.if_array(
        axis=0
    ),
    axis_name: Hashable = None,
    axis_size: Optional[int] = None,
) -> Callable:

    @wraps(func)
    def partition_and_recombine_fn(pytree: PyTree, *args: Any):

        @partial(
            eqx.filter_vmap,
            in_axes=(in_axes, None),
            out_axes=out_axes,
            axis_name=axis_name,
            axis_size=axis_size,
        )
        def recombine_fn(
            pytree_if_true_with_args: tuple[PyTree, ...],
            pytree_if_false: PyTree,
        ):
            pytree_if_true, *args = pytree_if_true_with_args

            pytree = eqx.combine(pytree_if_true, pytree_if_false, is_leaf=is_leaf)
            return func(pytree, *args)

        pytree_if_true, pytree_if_false = eqx.partition(
            pytree, filter_spec, is_leaf=is_leaf
        )
        return recombine_fn((pytree_if_true, *args), pytree_if_false)

    return partition_and_recombine_fn
