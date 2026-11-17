"""Seeded exogenous episodes; never reads equipment wear or agent actions."""
import random

def factors(seed, day):
    result = {}
    for index, kind in enumerate(('demand', 'supply', 'cost')):
        rng = random.Random(seed + index * 100003)
        onset = rng.randint(5, 15)
        value = 1.0
        while onset <= day:
            duration, recovery = rng.randint(8, 24), rng.randint(5, 12)
            peak = rng.uniform(1.25, 1.8)
            age = day - onset
            if age < duration:
                value = peak
                break
            if age < duration + recovery:
                value = 1 + (peak - 1) * (1 - (age - duration) / recovery)
                break
            onset += duration + recovery + rng.randint(10, 25)
        result[kind] = value
    return result
