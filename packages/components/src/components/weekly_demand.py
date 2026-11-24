"""Seeded weekly demand, installed by a consumer on the engine's world tick."""

from engine.world import WEEK, stream

from .registry import VERSION, Registry

DESCRIPTOR = {
    "version": VERSION,
    "type": "world.weekly_demand.v1",
    "instance_schema": {"roles": "array"},
    "state_schema": {},
    "handles": {},
    "emits": {},
    "parameters": {},
    "policies": {},
    "relations": {},
    "requires_adapters": [],
    "provides_adapters": [],
    "provenance": "https://github.com/debsagar/enterpriseengine/pull/11",
}


class WeeklyDemand:
    @staticmethod
    def validate(config):
        demand = config.get("world_demand")
        if (
            type(demand) is not dict
            or set(demand) != {"version", "roles"}
            or type(demand["version"]) is not int
            or demand["version"] != 1
        ):
            raise ValueError("world_demand requires version: 1 and roles")
        roles = demand["roles"]
        if (
            type(roles) is not list
            or not roles
            or any(type(role) is not str or not role.strip() for role in roles)
            or len(roles) != len(set(roles))
        ):
            raise ValueError("world_demand roles must be distinct nonempty role names")
        Registry([DESCRIPTOR]).resolve(
            {
                "version": VERSION,
                "adapters": [],
                "instances": [
                    {
                        "id": "weekly_demand",
                        "type": DESCRIPTOR["type"],
                        "config": {"roles": roles},
                        "state": {},
                        "parameters": {},
                        "policies": {},
                        "relations": {},
                    }
                ],
            }
        )

    @staticmethod
    def sample(previous, seed, at):
        return {"at": at, **demand_v1(previous, seed, at)}

    @staticmethod
    def observe(rows, config, role, cutoff):
        if role not in config["world_demand"]["roles"]:
            return []
        return [
            {
                "at": row["at"],
                "demand_forecast": row["demand_forecast"],
                "pos_units": row["pos_units"] if row["at"] + WEEK <= cutoff else None,
            }
            for row in rows
        ]


def demand_v1(previous, seed, at):
    """Advance one weekly demand sample using separate transition and noise streams."""
    regime, age = "normal", 0
    if previous is not None:
        current = previous["regime"]
        age = previous["age"] + 1
        coin = stream(seed, "demand-v1-regime", at).random()
        if current == "normal":
            if coin < 0.03:
                regime = "promo_spike"
            elif coin < 0.045:
                regime = "seasonal_lift"
            elif coin < 0.05:
                regime = "structural_decline"
        elif current == "promo_spike" and age < 4:
            regime = current
        elif current == "seasonal_lift" and age < 8 and coin <= 0.85:
            regime = current
        elif current == "structural_decline" and coin < 0.97:
            regime = current
        if regime != current:
            age = 0

    mean = {"normal": 20, "promo_spike": 26, "seasonal_lift": 30, "structural_decline": 14}[regime]
    if regime == "seasonal_lift" and age == 0:
        mean = 26
    pos = stream(seed, "demand-v1-realized", at).gauss(mean, 4)
    forecast = stream(seed, "demand-v1-forecast", at).gauss(mean, 6)
    return dict(
        regime=regime, age=age, mean=mean, pos_units=max(0, round(pos)), demand_forecast=max(0, round(forecast))
    )
