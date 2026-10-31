"""Comparing car setups setting by setting."""

from lmu_core.setups import compare, wheel_lock_deg


def wheels(name, fl, fr, rl, rr):
    return {f"WM_{name}-W_FL": fl, f"WM_{name}-W_FR": fr, f"WM_{name}-W_RL": rl,
            f"WM_{name}-W_RR": rr}


def test_settings_are_named_grouped_and_compared():
    mine = {"VM_REAR_WING": "8.8 deg", "VM_BRAKE_BALANCE": "52.5:47.5", "VM_FRONT_ANTISWAY": "P3",
            "VM_PITSTOP_1": "N/A", "VM_FUEL_CAPACITY": "85.0L (0.0 laps)", "VM_DIFF_PUMP": "N/A",
            "VM_GEAR_1": "Fixed", "VM_WEIGHT_DISTRIB": "Non-adjustable"}
    theirs = {**mine, "VM_REAR_WING": "9.5 deg"}
    rows = compare([mine, theirs])
    # Pit stop plans, the tank size and settings no lap has or can change are left out.
    assert [(r.group, r.name, r.differs) for r in rows] == [
        ("Driver settings", "Brake balance", False),
        ("Aero and cooling", "Rear wing", True),
        ("Suspension", "Front anti-roll bar", False),
    ]
    assert rows[1].values == ("8.8 deg", "9.5 deg")


def test_wheels_join_where_they_are_the_same():
    mine = {**wheels("CAMBER", "-2.2", "-2.2", "-1.9", "-1.9"),
            **wheels("PRESSURE", "136", "136", "136", "136"),
            **wheels("RIDEHEIGHT", "5.2", "5.3", "7.0", "7.0")}
    theirs = {**mine, **wheels("PRESSURE", "140", "140", "140", "140")}
    rows = {r.name: r.values for r in compare([mine, theirs])}
    assert rows["Camber front"] == ("-2.2", "-2.2") and rows["Camber rear"] == ("-1.9", "-1.9")
    assert rows["Tyre pressure"] == ("136", "140")
    assert rows["Ride height front left"] == ("5.2", "5.2")
    assert "Ride height rear right" in rows


def test_a_lap_without_a_setup_is_left_out_of_the_differences():
    rows = compare([{"VM_REAR_WING": "8.8 deg"}, None])
    assert rows[0].values == ("8.8 deg", None) and not rows[0].differs


def test_same_model_compares_steps_whatever_the_units():
    mine = {"WM_PRESSURE-W_FL": {"value": 0, "text": "136 kPa"},
            "VM_REAR_WING": {"value": 23, "text": "8.8 deg"},
            "VM_FRONT_TIRE_COMPOUND": {"value": 1, "text": "Medium"}}
    theirs = {"WM_PRESSURE-W_FL": {"value": 0, "text": "19.7 psi"},
              "VM_REAR_WING": {"value": 20, "text": "8.2 deg"},
              "VM_FRONT_TIRE_COMPOUND": {"value": 0, "text": "Medium"}}
    rows = {r.name: r for r in compare([mine, theirs])}
    assert not rows["Tyre pressure front left"].differs
    # The same tyre can have another step in another session.
    assert not rows["Front tyre compound"].differs
    assert rows["Tyre pressure front left"].values == ("136 kPa", "19.7 psi")
    assert rows["Rear wing"].differs
    # Other models' steps mean other things: by text.
    rows = {r.name: r for r in compare([mine, theirs], same_model=False)}
    assert rows["Tyre pressure front left"].differs


def test_wheel_lock_from_the_steering_lock_setting():
    assert wheel_lock_deg({"VM_STEER_LOCK": {"value": 6, "text": "524deg (18.5deg)"}}) == 18.5
    assert wheel_lock_deg({"VM_STEER_LOCK": {"value": 6, "text": "536 (18) deg"}}) == 18
    assert wheel_lock_deg({"VM_STEER_LOCK": {"value": 0, "text": "N/A"}}) is None
    assert wheel_lock_deg(None) is None
