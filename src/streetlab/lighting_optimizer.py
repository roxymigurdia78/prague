"""Outcome-adaptive proposals in normalized lighting coordinates.

Only exploration comparisons are used. Final evaluation views never train the
proposal distribution. This is a small adaptive Gaussian search, not CMA-ES.
"""
import math

import numpy as np


VERSION = 'adaptive-gaussian-v1'


class AdaptiveSampler:
    def __init__(self, state, parent, ranges, looks):
        self.parent = state['knobs'][parent]
        self.ranges = ranges
        self.keys = list(ranges)
        self.looks = looks
        rows = [row for step in state['history'] for row in step['trials']]
        by_id = {row['id']: row for row in rows}
        ancestors = {parent}
        pending = [parent]
        while pending:
            for ancestor in by_id.get(pending.pop(), {}).get('parents', []):
                if ancestor not in ancestors:
                    ancestors.add(ancestor)
                    pending.append(ancestor)

        local, successful = [], []
        for row in rows:
            # Full-view, order-swapped comparisons provide the learning signal.
            # Screening alone is too noisy to steer future parameter changes.
            for source, margin in row.get('confirmed_margins', {}).items():
                if source not in state['knobs']:
                    continue
                source_knobs = state['knobs'][source]
                if source in ancestors:
                    local.append((row, margin))
                if margin > 0:
                    successful.append((row, source_knobs, source in ancestors))

        recent = local[-24:]
        rate = sum(margin > 0 for _, margin in recent) / len(recent) if recent else .3
        self.scale = min(.24, max(.015, .10 * math.exp(2 * (rate - .3))))
        # A branch can reopen its neighborhood after repeated unsuccessful steps.
        failures = 0
        for _, margin in reversed(local):
            if margin > 0:
                break
            failures += 1
        self.scale = max(.015, self.scale * .85 ** min(failures, 5))
        if failures >= 6:
            self.scale = max(self.scale, min(.32, .12 + .02 * (failures - 6)))

        size = len(self.keys)
        moment = np.zeros((size, size))
        drift = np.zeros(size)
        weight_sum = 0.
        direction_weight = 0.
        look_weights = dict.fromkeys(looks, 1.)  # Keep every category possible.
        look_weights[self.parent['look']] += 3.
        for row, source_knobs, is_local in successful[-96:]:
            weight = 1. if is_local else .2
            step = self.encode(row['knobs']) - self.encode(source_knobs)
            length = float(np.linalg.norm(step))
            if length > 1e-9:
                unit = step / length
                moment += weight * size * np.outer(unit, unit)
                weight_sum += weight
                if is_local:
                    drift += unit * weight
                    direction_weight += weight
            look_weights[row['knobs']['look']] += weight
        learned = min(.75, weight_sum / (weight_sum + 4.))
        covariance = (1 - learned) * np.eye(size)
        if weight_sum:
            covariance += learned * moment / weight_sum
        self.transform = np.linalg.cholesky(covariance)
        self.drift = .35 * drift / direction_weight if direction_weight else drift
        self.look_weights = look_weights
        self.diagnostics = {'algorithm': VERSION, 'scale': round(self.scale, 6),
                            'confirmed_comparisons': len(local),
                            'successful_steps': len(successful),
                            'learned_weight': round(learned, 6),
                            'keys': self.keys,
                            'covariance': covariance.round(6).tolist()}

    @staticmethod
    def logarithmic(key):
        return key.endswith('_multiplier') or key.endswith('_power') or key.endswith('_offset')

    def position(self, key, value):
        low, high, _ = self.ranges[key]
        value = 6500 if value is None else value
        if self.logarithmic(key):
            return math.log(value / low) / math.log(high / low)
        return (value - low) / (high - low)

    def encode(self, knobs):
        return np.array([self.position(key, knobs[key]) for key in self.keys])

    def decode(self, positions, template):
        child = template.copy()
        for key, position in zip(self.keys, positions):
            low, high, _ = self.ranges[key]
            # Reflection avoids a pile-up of clipped samples at either bound.
            position = 1 - abs(float(position) % 2 - 1)
            value = low * (high / low) ** position if self.logarithmic(key) else low + (high - low) * position
            child[key] = round(value, 6)
        return child

    def sample(self, rng):
        normal = np.array([rng.gauss(0, 1) for _ in self.keys])
        step = self.scale * (self.transform @ normal + self.drift)
        child = self.decode(self.encode(self.parent) + step, self.parent)
        child['look'] = rng.choices(self.looks, weights=list(self.look_weights.values()))[0]
        return child

    def merge(self, other, rng):
        # Blend whole continuous settings so relationships between controls survive.
        fraction = rng.uniform(.15, .85)
        positions = (1 - fraction) * self.encode(self.parent) + fraction * self.encode(other)
        positions += np.array([rng.gauss(0, self.scale * .25) for _ in self.keys])
        child = self.decode(positions, self.parent)
        child['look'] = rng.choice((self.parent['look'], other['look']))
        return child

    def broad(self, rng):
        child = self.decode([rng.random() for _ in self.keys], self.parent)
        child['look'] = rng.choice(self.looks)
        return child
