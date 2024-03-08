"""
Voxel-based representations of the scattering potential.
"""

from abc import abstractmethod
from typing import (
    Any,
    Type,
    ClassVar,
    Optional,
    overload,
)
from typing_extensions import Self, override
from jaxtyping import Float, Array, Int
from functools import cached_property
from equinox import field, AbstractVar, AbstractClassVar

import equinox as eqx
import jax
import jax.numpy as jnp
import numpy as np

from ._scattering_potential import AbstractScatteringPotential
from .._pose import AbstractPose
from ...io import get_form_factor_params

from ...image.operators import AbstractFilter
from ...image import (
    pad_to_shape,
    crop_to_shape,
    fftn,
    compute_spline_coefficients,
)
from ...coordinates import CoordinateGrid, CoordinateList, FrequencySlice
from ...typing import (
    RealCloud,
    RealVolume,
    RealCubicVolume,
    ComplexCubicVolume,
    Real_,
)


class AbstractVoxelPotential(AbstractScatteringPotential, strict=True):
    """Abstract interface for a voxel-based scattering potential representation.

    **Attributes:**

    `voxel_size`: The voxel size of the scattering potential.

    `is_real`: Whether or not the representation is real or fourier-space.
    """

    voxel_size: AbstractVar[Real_]
    is_real: AbstractClassVar[bool]

    @property
    @abstractmethod
    def shape(self) -> tuple[int, ...]:
        """The shape of the voxel array."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def from_real_voxel_grid(
        cls: Type[Self],
        real_voxel_grid: Float[Array, "N N N"] | Float[np.ndarray, "N N N"],
        voxel_size: Float[Array, ""] | Float[np.ndarray, ""] | float = 1.0,
        **kwargs: Any,
    ) -> Self:
        """Load an `AbstractVoxels` from real-valued 3D electron
        scattering potential.
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def from_atoms(
        cls: Type[Self],
        atom_positions: Float[Array, "N 3"],
        atom_identities: Int[Array, "N"],
        voxel_size: Float[Array, ""] | Float[np.ndarray, ""] | float,
        coordinate_grid_in_angstroms: CoordinateGrid,
        form_factors: Optional[Float[Array, "N 5"]] = None,
        **kwargs: Any,
    ) -> Self:
        """Load an `AbstractVoxels` from atom positions and identities."""
        raise NotImplementedError


class AbstractFourierVoxelGrid(AbstractVoxelPotential, strict=True):
    """Abstract interface of a 3D scattering potential voxel grid
    in fourier-space.
    """

    frequency_slice: AbstractVar[FrequencySlice]

    @abstractmethod
    def __init__(
        self,
        fourier_voxel_grid: ComplexCubicVolume,
        frequency_slice: FrequencySlice,
        voxel_size: Real_,
    ):
        raise NotImplementedError

    @cached_property
    def frequency_slice_in_angstroms(self) -> FrequencySlice:
        """The `frequency_slice` in angstroms."""
        return self.frequency_slice / self.voxel_size

    def rotate_to_pose(self, pose: AbstractPose) -> Self:
        return eqx.tree_at(
            lambda d: d.frequency_slice.array,
            self,
            pose.rotate_coordinates(self.frequency_slice.get(), inverse=True),
        )

    @classmethod
    def from_real_voxel_grid(
        cls: Type[Self],
        real_voxel_grid: Float[Array, "N N N"] | Float[np.ndarray, "N N N"],
        voxel_size: Float[Array, ""] | Float[np.ndarray, ""] | float = 1.0,
        *,
        pad_scale: float = 1.0,
        pad_mode: str = "constant",
        filter: Optional[AbstractFilter] = None,
    ) -> Self:
        """Load an `AbstractFourierVoxelGrid` from real-valued 3D electron
        scattering potential voxel grid.

        **Arguments:**

        `real_voxel_grid`: A scattering potential voxel grid in real space.

        `voxel_size`: The voxel size of `real_voxel_grid`.

        `pad_scale`: Scale factor at which to pad `real_voxel_grid` before fourier
                     transform. Must be a value greater than `1.0`.

        `pad_mode`: Padding method. See `jax.numpy.pad` for documentation.

        `filter`: A filter to apply to the result of the fourier transform of
                  `real_voxel_grid`, i.e. `fftn(real_voxel_grid)`. Note that the zero
                  frequency component is assumed to be in the corner.
        """
        # Cast to jax array
        real_voxel_grid, voxel_size = jnp.asarray(real_voxel_grid), jnp.asarray(
            voxel_size
        )
        # Pad template
        if pad_scale < 1.0:
            raise ValueError("pad_scale must be greater than 1.0")
        # ... always pad to even size to avoid interpolation issues in
        # fourier slice extraction.
        padded_shape = tuple([int(s * pad_scale) for s in real_voxel_grid.shape])
        padded_real_voxel_grid = pad_to_shape(
            real_voxel_grid, padded_shape, mode=pad_mode
        )
        # Load potential and coordinates. For now, do not store the
        # fourier potential only on the half space. Fourier slice extraction
        # does not currently work if rfftn is used.
        fourier_voxel_grid_with_zero_in_corner = (
            fftn(padded_real_voxel_grid)
            if filter is None
            else filter(fftn(padded_real_voxel_grid))
        )
        # ... store the potential grid with the zero frequency component in the center
        fourier_voxel_grid = jnp.fft.fftshift(fourier_voxel_grid_with_zero_in_corner)
        # ... create in-plane frequency slice on the half space
        frequency_slice = FrequencySlice(
            padded_real_voxel_grid.shape[:-1], half_space=False
        )

        return cls(fourier_voxel_grid, frequency_slice, voxel_size)

    @classmethod
    def from_atoms(
        cls: Type[Self],
        atom_positions: Float[Array, "N 3"],
        atom_identities: Int[Array, "N"],
        voxel_size: Float[Array, ""] | Float[np.ndarray, ""] | float,
        coordinate_grid_in_angstroms: CoordinateGrid,
        form_factors: Optional[Float[Array, "N 5"]] = None,
        **kwargs: Any,
    ) -> Self:
        """Load an `AbstractFourierVoxelGrid` from atom positions and identities.

        **Arguments:**

        - `**kwargs`: Passed to `AbstractFourierVoxelGrid.from_real_voxel_grid`
        """
        a_vals, b_vals = get_form_factor_params(atom_identities, form_factors)

        potential = build_real_space_voxels_from_atoms(
            atom_positions, a_vals, b_vals, coordinate_grid_in_angstroms.get()
        )

        return cls.from_real_voxel_grid(
            potential,
            voxel_size,
            **kwargs,
        )


class FourierVoxelGrid(AbstractFourierVoxelGrid):
    """A 3D scattering potential voxel grid in fourier-space.

    **Attributes:**

    `fourier_voxel_grid`: The cubic voxel grid in fourier space.

    `frequency_slice`: Frequency slice coordinate system.

    `voxel_size`: The voxel size.
    """

    fourier_voxel_grid: ComplexCubicVolume = field(converter=jnp.asarray)
    frequency_slice: FrequencySlice
    voxel_size: Real_ = field(converter=jnp.asarray)

    is_real: ClassVar[bool] = False

    @override
    def __init__(
        self,
        fourier_voxel_grid: ComplexCubicVolume,
        frequency_slice: FrequencySlice,
        voxel_size: Real_,
    ):
        self.fourier_voxel_grid = fourier_voxel_grid
        self.frequency_slice = frequency_slice
        self.voxel_size = voxel_size

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.fourier_voxel_grid.shape


class FourierVoxelGridInterpolator(AbstractFourierVoxelGrid):
    """A 3D scattering potential voxel grid in fourier-space, represented
    by spline coefficients.

    **Attributes:**

    `coefficients`: Cubic spline coefficients for the voxel grid.

    `frequency_slice`: Frequency slice coordinate system.

    `voxel_size`: The voxel size.
    """

    coefficients: ComplexCubicVolume = field(converter=jnp.asarray)
    frequency_slice: FrequencySlice
    voxel_size: Real_ = field(converter=jnp.asarray)

    is_real: ClassVar[bool] = False

    def __init__(
        self,
        fourier_voxel_grid: ComplexCubicVolume,
        frequency_slice: FrequencySlice,
        voxel_size: Real_,
    ):
        """
        !!! note
            The argument `fourier_voxel_grid` is used to set
            `FourierVoxelGridInterpolator.coefficients` in the `__init__`.
            For example,

            ```python
            voxels = FourierVoxelGridInterpolator(fourier_voxel_grid, frequency_slice, voxel_size)
            assert not hasattr(voxels, "fourier_voxel_grid")  # This does not store the `fourier_voxel_grid`
            assert hasattr(voxels, "coefficients")  # Instead it computes `coefficients` upon `__init__`
            ```
        """
        self.coefficients = compute_spline_coefficients(fourier_voxel_grid)
        self.frequency_slice = frequency_slice
        self.voxel_size = voxel_size

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple([s - 2 for s in self.coefficients.shape])


class RealVoxelGrid(AbstractVoxelPotential, strict=True):
    """Abstraction of a 3D scattering potential voxel grid in real-space.

    **Attributes:**

    `real_voxel_grid`: The voxel grid in fourier space.

    `coordinate_grid`: A coordinate grid.

    `voxel_size`: The voxel size.
    """

    real_voxel_grid: RealCubicVolume = field(converter=jnp.asarray)
    coordinate_grid: CoordinateGrid
    voxel_size: Real_ = field(converter=jnp.asarray)

    is_real: ClassVar[bool] = True

    def __init__(
        self,
        real_voxel_grid: RealCubicVolume,
        coordinate_grid: CoordinateGrid,
        voxel_size: Real_,
    ):
        self.real_voxel_grid = real_voxel_grid
        self.coordinate_grid = coordinate_grid
        self.voxel_size = voxel_size

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.real_voxel_grid.shape

    @cached_property
    def coordinate_grid_in_angstroms(self) -> CoordinateGrid:
        """The `coordinate_grid` in angstroms."""
        return self.voxel_size * self.coordinate_grid

    def rotate_to_pose(self, pose: AbstractPose) -> Self:
        return eqx.tree_at(
            lambda d: d.coordinate_grid.array,
            self,
            pose.rotate_coordinates(self.coordinate_grid.get(), inverse=False),
        )

    @overload
    @classmethod
    def from_real_voxel_grid(
        cls: Type[Self],
        real_voxel_grid: Float[Array, "N N N"] | Float[np.ndarray, "N N N"],
        voxel_size: Float[Array, ""] | Float[np.ndarray, ""] | float,
        coordinate_grid: CoordinateGrid,
    ) -> Self: ...

    @overload
    @classmethod
    def from_real_voxel_grid(
        cls: Type[Self],
        real_voxel_grid: Float[Array, "N N N"] | Float[np.ndarray, "N N N"],
        voxel_size: Float[Array, ""] | Float[np.ndarray, ""] | float,
        *,
        crop_scale: Optional[float],
    ) -> Self: ...

    @classmethod
    def from_real_voxel_grid(
        cls: Type[Self],
        real_voxel_grid: Float[Array, "N N N"] | Float[np.ndarray, "N N N"],
        voxel_size: Float[Array, ""] | Float[np.ndarray, ""] | float,
        coordinate_grid: Optional[CoordinateGrid] = None,
        *,
        crop_scale: Optional[float] = None,
    ) -> Self:
        """Load a `RealVoxelGrid` from a real-valued 3D electron
        scattering potential voxel grid.

        !!! warning
            `real_voxel_grid` is transposed upon instantiation in order to make
            the results of `cryojax.simulator.NufftProject` agree with
            `cryojax.simulator.FourierSliceExtract`.

            ```python
            real_voxel_grid = ...
            potential = RealVoxelGrid.from_real_voxel_grid(real_voxel_grid, ...)
            assert real_voxel_grid == jnp.transpose(potential.real_voxel_grid, axes=[1, 0, 2])
            ```

        **Arguments:**

        `real_voxel_grid`: An electron scattering potential voxel grid in real space.

        `voxel_size`: The voxel size of `real_voxel_grid`.

        `crop_scale`: Scale factor at which to crop `real_voxel_grid`.
                      Must be a value less than `1.0`.
        """
        # Cast to jax array
        real_voxel_grid, voxel_size = jnp.asarray(real_voxel_grid), jnp.asarray(
            voxel_size
        )
        # A nasty hack to make NufftProject agree with FourierSliceExtract
        real_voxel_grid = jnp.transpose(real_voxel_grid, axes=[1, 0, 2])
        # Make coordinates if not given
        if coordinate_grid is None:
            # Option for cropping template
            if crop_scale is not None:
                if crop_scale > 1.0:
                    raise ValueError("crop_scale must be less than 1.0")
                cropped_shape = tuple(
                    [int(s * crop_scale) for s in real_voxel_grid.shape[-3:]]
                )
                real_voxel_grid = crop_to_shape(real_voxel_grid, cropped_shape)
            coordinate_grid = CoordinateGrid(real_voxel_grid.shape[-3:])

        return cls(real_voxel_grid, coordinate_grid, voxel_size)

    @classmethod
    def from_atoms(
        cls: Type[Self],
        atom_positions: Float[Array, "N 3"],
        atom_identities: Int[Array, "N"],
        voxel_size: Float[Array, ""] | Float[np.ndarray, ""] | float,
        coordinate_grid_in_angstroms: CoordinateGrid,
        form_factors: Optional[Float[Array, "N 5"]] = None,
        **kwargs: Any,
    ) -> Self:
        """Load a `RealVoxelGrid` from atom positions and identities.

        **Arguments:**

        - `**kwargs`: Passed to `RealVoxelGrid.from_real_voxel_grid`
        """
        a_vals, b_vals = get_form_factor_params(atom_identities, form_factors)

        real_voxel_grid = build_real_space_voxels_from_atoms(
            atom_positions, a_vals, b_vals, coordinate_grid_in_angstroms.get()
        )

        return cls.from_real_voxel_grid(
            real_voxel_grid,
            voxel_size,
            coordinate_grid_in_angstroms / voxel_size,
            **kwargs,
        )


class RealVoxelCloud(AbstractVoxelPotential, strict=True):
    """Abstraction of a 3D electron scattering potential voxel point cloud.

    !!! info
        This object is similar to the `RealVoxelGrid`. Instead
        of storing the whole voxel grid, a `RealVoxelCloud` need
        only store points of non-zero scattering potential. Therefore,
        a `RealVoxelCloud` stores a point cloud of scattering potential
        voxel values.

    **Attributes:**

    `voxel_weights`: A point-cloud of voxel scattering potential values.

    `coordinate_list`: Coordinate list for the `voxel_weights`.

    `voxel_size`: The voxel size.
    """

    voxel_weights: RealCloud = field(converter=jnp.asarray)
    coordinate_list: CoordinateList
    voxel_size: Real_ = field(converter=jnp.asarray)

    is_real: ClassVar[bool] = True

    def __init__(
        self,
        voxel_weights: RealCloud,
        coordinate_list: CoordinateList,
        voxel_size: Real_,
    ):
        self.voxel_weights = voxel_weights
        self.coordinate_list = coordinate_list
        self.voxel_size = voxel_size

    @property
    def shape(self) -> tuple[int, int]:
        return self.voxel_weights.shape

    @cached_property
    def coordinate_list_in_angstroms(self) -> CoordinateList:
        """The `coordinate_list` in angstroms."""
        return self.voxel_size * self.coordinate_list

    def rotate_to_pose(self, pose: AbstractPose) -> Self:
        return eqx.tree_at(
            lambda d: d.coordinate_list.array,
            self,
            pose.rotate_coordinates(self.coordinate_list.get(), inverse=False),
        )

    @classmethod
    def from_real_voxel_grid(
        cls: Type[Self],
        real_voxel_grid: Float[Array, "N N N"] | Float[np.ndarray, "N N N"],
        voxel_size: Float[Array, ""] | Float[np.ndarray, ""] | float,
        coordinate_grid: Optional[CoordinateGrid] = None,
        *,
        rtol: float = 1e-05,
        atol: float = 1e-08,
    ) -> Self:
        """Load an `RealVoxelCloud` from a real-valued 3D electron
        scattering potential voxel grid.

        !!! warning
            `real_voxel_grid` is transposed upon instantiation in order to make
            the results of `cryojax.simulator.NufftProject` agree with
            `cryojax.simulator.FourierSliceExtract`.
            See [`cryojax.simulator.RealVoxelGrid`][] for more detail.

        **Arguments:**

        `real_voxel_grid`: An electron scattering potential voxel grid in real space.

        `voxel_size`: The voxel size of `real_voxel_grid`.

        `rtol`: Argument passed to `jnp.isclose`, used for removing
                points of zero scattering potential.

        `atol`: Argument passed to `jnp.isclose`, used for removing
                points of zero scattering potential.
        """
        # Cast to jax array
        real_voxel_grid, voxel_size = jnp.asarray(real_voxel_grid), jnp.asarray(
            voxel_size
        )
        # A nasty hack to make NufftProject agree with FourierSliceExtract
        real_voxel_grid = jnp.transpose(real_voxel_grid, axes=[1, 0, 2])
        # Make coordinates if not given
        if coordinate_grid is None:
            coordinate_grid = CoordinateGrid(real_voxel_grid.shape)
        # ... mask zeros to store smaller arrays. This
        # option is not jittable.
        nonzero = jnp.where(~jnp.isclose(real_voxel_grid, 0.0, rtol=rtol, atol=atol))
        flat_potential = real_voxel_grid[nonzero]
        coordinate_list = CoordinateList(coordinate_grid.get()[nonzero])

        return cls(flat_potential, coordinate_list, voxel_size)

    @classmethod
    def from_atoms(
        cls: Type[Self],
        atom_positions: Float[Array, "N 3"],
        atom_identities: Int[Array, "N"],
        voxel_size: Float[Array, ""] | Float[np.ndarray, ""] | float,
        coordinate_grid_in_angstroms: CoordinateGrid,
        form_factors: Optional[Float[Array, "N 5"]] = None,
        **kwargs: Any,
    ) -> Self:
        """Load a `RealVoxelCloud` from atom positions and identities.

        **Arguments:**

        - `**kwargs`: Passed to `RealVoxelCloud.from_real_voxel_grid`
        """
        a_vals, b_vals = get_form_factor_params(atom_identities, form_factors)

        real_voxel_grid = build_real_space_voxels_from_atoms(
            atom_positions, a_vals, b_vals, coordinate_grid_in_angstroms.get()
        )

        return cls.from_real_voxel_grid(
            real_voxel_grid,
            voxel_size,
            coordinate_grid_in_angstroms / voxel_size,
            **kwargs,
        )


def evaluate_3d_real_space_gaussian(
    coordinate_grid_in_angstroms: Float[Array, "N1 N2 N3 3"],
    atom_position: Float[Array, "3"],
    a: float,
    b: float,
) -> Float[Array, "N1 N2 N3"]:
    """Evaluate a gaussian on a 3D grid.
    The naming convention for parameters follows "Robust
    Parameterization of Elastic and Absorptive Electron Atomic Scattering
    Factors" by Peng et al.

    **Arguments:**

    `coordinate_grid`: The coordinate system of the grid.

    `pos`: The center of the gaussian.

    `a`: A scale factor.

    `b`: The scale of the gaussian.

    **Returns:**

    The potential of the gaussian on the grid.
    """
    b_inverse = 4.0 * jnp.pi / b
    sq_distances = jnp.sum(
        b_inverse * (coordinate_grid_in_angstroms - atom_position) ** 2, axis=-1
    )
    return jnp.exp(-jnp.pi * sq_distances) * a * b_inverse ** (3.0 / 2.0)


def evaluate_3d_atom_potential(
    coordinate_grid_in_angstroms: Float[Array, "N1 N2 N3 3"],
    atom_position: Float[Array, "3"],
    atomic_as: Float[Array, "5"],
    atomic_bs: Float[Array, "5"],
) -> Float[Array, "N1 N2 N3"]:
    """Evaluates the electron potential of a single atom on a 3D grid.

    **Arguments:**

    `coordinate_grid_in_angstroms`: The coordinate system of the grid.

    `atom_position`: The location of the atom.

    `atomic_as`: The intensity values for each gaussian in the atom.

    `atomic_bs`: The inverse scale factors for each gaussian in the atom.

    **Returns:**

    The potential of the atom evaluated on the grid.
    """
    eval_fxn = jax.vmap(evaluate_3d_real_space_gaussian, in_axes=(None, None, 0, 0))
    return jnp.sum(
        eval_fxn(coordinate_grid_in_angstroms, atom_position, atomic_as, atomic_bs),
        axis=0,
    )


@jax.jit
def build_real_space_voxels_from_atoms(
    atom_positions: Float[Array, "N 3"],
    ff_a: Float[Array, "N 5"],
    ff_b: Float[Array, "N 5"],
    coordinate_grid_in_angstroms: Float[Array, "N1 N2 N3 3"],
) -> RealCubicVolume:
    """
    Build a voxel representation of an atomic model.

    **Arguments**

    `atom_coords`: The coordinates of the atoms.

    `ff_a`: Intensity values for each Gaussian in the atom

    `ff_b` : The inverse scale factors for each Gaussian in the atom

    `coordinate_grid` : The coordinates of each voxel in the grid.

    **Returns:**

    The voxel representation of the atomic model.
    """
    voxel_grid_buffer = jnp.zeros(coordinate_grid_in_angstroms.shape[:-1])

    def add_gaussian_to_potential(i, potential):
        potential += evaluate_3d_atom_potential(
            coordinate_grid_in_angstroms, atom_positions[i], ff_a[i], ff_b[i]
        )
        return potential

    voxel_grid = jax.lax.fori_loop(
        0, atom_positions.shape[0], add_gaussian_to_potential, voxel_grid_buffer
    )

    return voxel_grid