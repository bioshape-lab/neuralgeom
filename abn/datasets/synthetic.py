"""Generate and load synthetic datasets."""
import logging

import numpy as np
import pandas as pd
import skimage


def load_projections(n_scalars=5, n_angles=1000, img_size=128):
    """Load a dataset of 2D images projected into 1D projections.

    The actions are:
    - action of SO(2): rotation
    - action of R^_+: blur

    Parameters
    ----------
    n_scalars : int
        Number of scalar used for action of scalings.
    n_angles : int
        Number of angles used for action of SO(2).

    Returns
    -------
    projections : array-like, shape=[n_scalars * n_angles, img_size]]
        Projections with different orientations and blurs.
    labels : pd.DataFrame, shape=[n_scalars * n_angles, 2]
        Labels organized in 2 columns: angles, and scalars.
    """
    images, labels = load_images(
        n_scalars=n_scalars, n_angles=n_angles, img_size=img_size
    )

    projections = np.sum(images, axis=-1)
    return projections, labels


def load_images(n_scalars=4, n_angles=2000, img_size=128):
    """Load a dataset of images.

    The actions are:
    - action of SO(2): rotation
    - action of R^_+: blur

    Parameters
    ----------
    n_scalars : int
        Number of scalar used for action of scalings.
    n_angles : int
        Number of angles used for action of SO(2).

    Returns
    -------
    images : array-like, shape=[n_scalars * n_angles, img_size, img_size]]
        Images with different orientations and blurs.
    labels : pd.DataFrame, shape=[n_scalars * n_angles, 2]
        Labels organized in 2 columns: angles, and scalars.
    """
    logging.info("Generating dataset of synthetic images.")
    image = skimage.data.camera()
    image = skimage.transform.resize(image, (img_size, img_size), anti_aliasing=True)

    images = []
    angles = []
    scalars = []
    for i_angle in range(n_angles):
        angle = 360 * i_angle / n_angles
        rot_image = skimage.transform.rotate(image, angle)
        for i_scalar in range(n_scalars):
            scalar = 1 + 0.2 * i_scalar
            blur_image = skimage.filters.gaussian(rot_image, sigma=scalar)
            noise = np.random.normal(loc=0.0, scale=0.05, size=blur_image.shape)
            images.append((blur_image + noise).astype(np.float32))
            angles.append(angle)
            scalars.append(scalar)

    labels = pd.DataFrame(
        {
            "angles": angles,
            "scalars": scalars,
        }
    )
    return np.array(images), labels


def load_points(n_scalars=10, n_angles=100):
    """Load a dataset of points in R^3.

    The actions are:
    - action of SO(2): along z-axis
    - action of R^_+

    Parameters
    ----------
    n_scalars : int
        Number of scalar used for action of scalings.
    n_angles : int
        Number of angles used for action of SO(2).

    Returns
    -------
    points : array-like, shape=[n_scalars * n_angles, 3]
        Points sampled on a cone.
    labels : pd.DataFrame, shape=[n_scalars * n_angles, 2]
        Labels organized in 2 columns: angles, and scalars.
    """
    points = []
    angles = []
    scalars = []
    point = np.array([1, 1, 1])
    for i_angle in range(n_angles):
        angle = 2 * np.pi * i_angle / n_angles
        rotmat = np.array(
            [
                [np.cos(angle), -np.sin(angle), 0],
                [np.sin(angle), np.cos(angle), 0],
                [0, 0, 1.0],
            ]
        )
        rot_point = rotmat @ point
        for i_scalar in range(n_scalars):
            scalar = 1 + i_scalar
            points.append(scalar * rot_point)

            angles.append(angle)
            scalars.append(scalar)

    labels = pd.DataFrame(
        {
            "angles": angles,
            "scalars": scalars,
        }
    )

    return np.array(points), labels


def load_place_cells(n_times=10000, n_cells=40):
    """Load synthetic place cells.

    This is a dataset of synthetic place cell firings, that
    simulates a rat walking in a circle.

    Each place cell activated (2 firings) also activates
    its neighbors (1 firing each) to simulate the circular
    relationship.

    Parameters
    ----------
    n_times : int
        Number of times.
    n_cells : int
        Number of place cells.

    Returns
    -------
    place_cells : array-like, shape=[n_times, n_cells]
        Number of firings per time step and per cell.
    labels : pd.DataFrame, shape=[n_timess, 1]
        Labels organized in 1 column: angles.
    """
    n_firing_per_cell = int(n_times / n_cells)
    place_cells = []
    labels = []
    for _ in range(n_firing_per_cell):
        for i_cell in range(n_cells):
            cell_firings = np.zeros(n_cells)

            if i_cell == 0:
                cell_firings[-2] = np.random.poisson(1.0)
                cell_firings[-1] = np.random.poisson(2.0)
                cell_firings[0] = np.random.poisson(4.0)
                cell_firings[1] = np.random.poisson(2.0)
                cell_firings[2] = np.random.poisson(1.0)
            elif i_cell == 1:
                cell_firings[-1] = np.random.poisson(1.0)
                cell_firings[0] = np.random.poisson(2.0)
                cell_firings[1] = np.random.poisson(4.0)
                cell_firings[2] = np.random.poisson(2.0)
                cell_firings[3] = np.random.poisson(1.0)
            elif i_cell == n_cells - 2:
                cell_firings[-4] = np.random.poisson(1.0)
                cell_firings[-3] = np.random.poisson(2.0)
                cell_firings[-2] = np.random.poisson(4.0)
                cell_firings[-1] = np.random.poisson(2.0)
                cell_firings[0] = np.random.poisson(1.0)
            elif i_cell == n_cells - 1:
                cell_firings[-3] = np.random.poisson(1.0)
                cell_firings[-2] = np.random.poisson(2.0)
                cell_firings[-1] = np.random.poisson(4.0)
                cell_firings[0] = np.random.poisson(2.0)
                cell_firings[1] = np.random.poisson(1.0)
            else:
                cell_firings[i_cell - 2] = np.random.poisson(1.0)
                cell_firings[i_cell - 1] = np.random.poisson(2.0)
                cell_firings[i_cell] = np.random.poisson(4.0)
                cell_firings[i_cell + 1] = np.random.poisson(2.0)
                cell_firings[i_cell - 3] = np.random.poisson(1.0)
            place_cells.append(cell_firings)
            labels.append(i_cell / n_cells * 360)

    return np.array(place_cells), pd.DataFrame({"angles": labels})
