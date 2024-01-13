"""
Abstractions of a biological specimen.
"""

from __future__ import annotations

__all__ = ["Ensemble", "Conformation"]

from typing import Optional
from functools import cached_property

import jax
import jax.numpy as jnp
from equinox import Module

from .density import ElectronDensity
from .pose import Pose, EulerPose
from ..core import field
from ..typing import Int_


class Ensemble(Module):
    """
    Abstraction of an ensemble of biological specimen.

    Attributes
    ----------
    density :
        The electron density representation of the
        specimen.
    conformation :
        The conformational variable at which to evaulate
        the electron density. Use this variable when, for example,
        the specimen ``ElectronDensity`` is constructed
        with ``ElectronDensity.from_list``.
    pose :
        The pose of the specimen.
    """

    density: ElectronDensity
    pose: Pose
    conformation: Optional[Conformation] = None

    def __init__(
        self,
        density: ElectronDensity,
        pose: Optional[Pose] = None,
        conformation: Optional[Conformation] = None,
    ):
        self.density = density
        self.pose = pose or EulerPose()
        self.conformation = None if conformation is None else conformation

    def __check_init__(self):
        if self.density.n_stacked_dims != 1 and self.conformation is not None:
            raise ValueError(
                "ElectronDensity.n_stacked_dims must be 1 if conformation is set."
            )

    @cached_property
    def density_at_conformation_and_pose(self) -> ElectronDensity:
        """Get the electron density at the configured pose and conformation."""
        if self.conformation is None:
            density = self.density
        else:
            funcs = [
                lambda i=i: self.density[i] for i in range(len(self.density))
            ]
            density = jax.lax.switch(self.conformation.get(), funcs)

        return density.rotate_to_pose(self.pose)


class Conformation(Module):
    """
    A conformational variable wrapped in a Module.
    """

    _value: Int_ = field(converter=jnp.asarray)

    def get(self) -> Int_:
        return self._value