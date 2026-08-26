"""Constant-velocity Kalman filter for bounding boxes.

State is 8-dimensional: (cx, cy, aspect, height) and their velocities. Tracking
aspect ratio rather than width is the SORT/ByteTrack convention and it matters
here — a vehicle approaching a camera changes height fast while its aspect stays
roughly constant, so the filter stays stable through the approach.

Process and measurement noise scale with box height, which is a proxy for
distance: a far-off car's box is jittery in absolute pixels but not in relative
terms, and a fixed noise model would either over-smooth near vehicles or lose
distant ones.
"""

from __future__ import annotations

import numpy as np
import scipy.linalg


class KalmanFilter:
    """Tracks (cx, cy, a, h) with constant velocity."""

    def __init__(self) -> None:
        ndim, dt = 4, 1.0

        self._motion_mat = np.eye(2 * ndim, 2 * ndim)
        for i in range(ndim):
            self._motion_mat[i, ndim + i] = dt
        self._update_mat = np.eye(ndim, 2 * ndim)

        # Uncertainty relative to box height.
        self._std_weight_position = 1.0 / 20
        self._std_weight_velocity = 1.0 / 160

    def initiate(self, measurement: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Start a track from one observation, with zero initial velocity."""
        mean = np.r_[measurement, np.zeros_like(measurement)]
        h = measurement[3]
        std = [
            2 * self._std_weight_position * h,
            2 * self._std_weight_position * h,
            1e-2,
            2 * self._std_weight_position * h,
            10 * self._std_weight_velocity * h,
            10 * self._std_weight_velocity * h,
            1e-5,
            10 * self._std_weight_velocity * h,
        ]
        return mean, np.diag(np.square(std))

    def predict(self, mean: np.ndarray, covariance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = mean[3]
        std_pos = [
            self._std_weight_position * h,
            self._std_weight_position * h,
            1e-2,
            self._std_weight_position * h,
        ]
        std_vel = [
            self._std_weight_velocity * h,
            self._std_weight_velocity * h,
            1e-5,
            self._std_weight_velocity * h,
        ]
        motion_cov = np.diag(np.square(np.r_[std_pos, std_vel]))

        mean = self._motion_mat @ mean
        covariance = self._motion_mat @ covariance @ self._motion_mat.T + motion_cov
        return mean, covariance

    def multi_predict(
        self, means: np.ndarray, covariances: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Vectorised predict for every active track at once.

        With a few dozen vehicles in frame this is the difference between the
        filter being free and the filter being measurable.
        """
        if len(means) == 0:
            return means, covariances

        h = means[:, 3]
        zeros = np.zeros_like(h)
        std_pos = np.stack(
            [
                self._std_weight_position * h,
                self._std_weight_position * h,
                zeros + 1e-2,
                self._std_weight_position * h,
            ],
            axis=1,
        )
        std_vel = np.stack(
            [
                self._std_weight_velocity * h,
                self._std_weight_velocity * h,
                zeros + 1e-5,
                self._std_weight_velocity * h,
            ],
            axis=1,
        )
        motion_cov = np.stack(
            [np.diag(sq) for sq in np.square(np.concatenate([std_pos, std_vel], axis=1))]
        )

        means = means @ self._motion_mat.T
        covariances = (
            self._motion_mat @ covariances @ self._motion_mat.T + motion_cov
        )
        return means, covariances

    def project(self, mean: np.ndarray, covariance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = mean[3]
        std = [
            self._std_weight_position * h,
            self._std_weight_position * h,
            1e-1,
            self._std_weight_position * h,
        ]
        innovation_cov = np.diag(np.square(std))
        mean = self._update_mat @ mean
        covariance = self._update_mat @ covariance @ self._update_mat.T
        return mean, covariance + innovation_cov

    def update(
        self, mean: np.ndarray, covariance: np.ndarray, measurement: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        projected_mean, projected_cov = self.project(mean, covariance)

        chol_factor, lower = scipy.linalg.cho_factor(
            projected_cov, lower=True, check_finite=False
        )
        kalman_gain = scipy.linalg.cho_solve(
            (chol_factor, lower),
            (covariance @ self._update_mat.T).T,
            check_finite=False,
        ).T
        innovation = measurement - projected_mean

        new_mean = mean + innovation @ kalman_gain.T
        new_covariance = covariance - kalman_gain @ projected_cov @ kalman_gain.T
        return new_mean, new_covariance
