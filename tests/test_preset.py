"""Tests for the Preset model and PresetStore."""

from __future__ import annotations

import json
import os
import stat

import pytest

from vpnpilot.preset import (
    Preset,
    PresetFlags,
    PresetStore,
    PresetTarget,
    TargetKind,
    decode_city_target,
    encode_city_target,
    preset_from_dict,
    preset_to_connect_kwargs,
    preset_to_dict,
)

# ---- model validation -------------------------------------------------


def test_target_none_normalizes_empty_value():
    t = PresetTarget(kind=TargetKind.NONE, value="ignored")
    assert t.value == ""


def test_target_city_requires_value():
    with pytest.raises(ValueError):
        PresetTarget(kind=TargetKind.CITY, value="")
    with pytest.raises(ValueError):
        PresetTarget(kind=TargetKind.CITY, value="   ")


def test_target_country_requires_value():
    with pytest.raises(ValueError):
        PresetTarget(kind=TargetKind.COUNTRY, value="")


def test_flags_allow_one_modifier():
    PresetFlags(p2p=True)
    PresetFlags(secure_core=True)
    PresetFlags(tor=True)
    PresetFlags(random=True)
    PresetFlags(p2p=True, random=True)  # random is orthogonal


def test_flags_reject_two_modifiers():
    with pytest.raises(ValueError):
        PresetFlags(p2p=True, secure_core=True)
    with pytest.raises(ValueError):
        PresetFlags(secure_core=True, tor=True)
    with pytest.raises(ValueError):
        PresetFlags(p2p=True, tor=True)


# ---- round-trip -------------------------------------------------------


def test_preset_dict_round_trip():
    p = Preset.new(
        name="LA P2P",
        target=PresetTarget(kind=TargetKind.CITY, value="Los Angeles"),
        flags=PresetFlags(p2p=True, random=True),
    )
    d = preset_to_dict(p)
    p2 = preset_from_dict(d)
    assert p == p2


def test_preset_dict_round_trip_scoped_city_value():
    p = Preset.new(
        name="Seattle (US)",
        target=PresetTarget(kind=TargetKind.CITY, value="US::Seattle"),
    )
    d = preset_to_dict(p)
    assert d["target"]["value"] == "US::Seattle"
    p2 = preset_from_dict(d)
    assert p2.target.value == "US::Seattle"


def test_city_target_encode_decode_round_trip():
    value = encode_city_target("Seattle", "US")
    assert value == "US::Seattle"
    code, city = decode_city_target(value)
    assert code == "US"
    assert city == "Seattle"


def test_city_target_decode_legacy_unscoped_value():
    code, city = decode_city_target("Seattle")
    assert code is None
    assert city == "Seattle"


def test_preset_to_connect_kwargs_city():
    p = Preset.new(
        name="Seattle",
        target=PresetTarget(kind=TargetKind.CITY, value="Seattle"),
    )
    assert preset_to_connect_kwargs(p) == {"city": "Seattle"}


def test_preset_to_connect_kwargs_city_scoped():
    p = Preset.new(
        name="Seattle US",
        target=PresetTarget(kind=TargetKind.CITY, value="US::Seattle"),
    )
    assert preset_to_connect_kwargs(p) == {"city": "Seattle"}


def test_preset_to_connect_kwargs_country_p2p():
    p = Preset.new(
        name="US P2P",
        target=PresetTarget(kind=TargetKind.COUNTRY, value="US"),
        flags=PresetFlags(p2p=True),
    )
    assert preset_to_connect_kwargs(p) == {"country": "US", "p2p": True}


def test_preset_to_connect_kwargs_server_id():
    p = Preset.new(
        name="Specific",
        target=PresetTarget(kind=TargetKind.SERVER_ID, value="US-WA#187"),
    )
    assert preset_to_connect_kwargs(p) == {"server_id": "US-WA#187"}


def test_preset_to_connect_kwargs_fastest_securecore():
    p = Preset.new(
        name="Secure Core",
        target=PresetTarget(kind=TargetKind.NONE),
        flags=PresetFlags(secure_core=True),
    )
    assert preset_to_connect_kwargs(p) == {"secure_core": True}


def test_preset_to_connect_kwargs_random_renamed():
    # Preset.flags.random maps to cli kwarg "random_server" (which is
    # the existing ProtonCLI.connect signature — `random` is a Python
    # builtin).
    p = Preset.new(
        name="Random US",
        target=PresetTarget(kind=TargetKind.COUNTRY, value="US"),
        flags=PresetFlags(random=True),
    )
    assert preset_to_connect_kwargs(p) == {"country": "US", "random_server": True}


# ---- store: seeding ---------------------------------------------------


def _path(tmp_path):
    return tmp_path / "presets.json"


def test_seed_on_first_load_persists(tmp_path):
    p = _path(tmp_path)
    store = PresetStore(path=p)
    presets = store.load()
    assert len(presets) == 1
    assert presets[0].name == "Seattle"
    assert presets[0].is_default is True
    assert p.exists()
    doc = json.loads(p.read_text())
    assert doc["version"] == 1
    assert doc["presets"][0]["name"] == "Seattle"


def test_load_is_idempotent(tmp_path):
    p = _path(tmp_path)
    store = PresetStore(path=p)
    first = store.load()
    second = store.load()
    assert first == second


def test_file_perms_0600(tmp_path):
    p = _path(tmp_path)
    PresetStore(path=p).load()
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


# ---- store: corrupt recovery -----------------------------------------


def test_corrupt_json_backed_up_and_reseeded(tmp_path):
    p = _path(tmp_path)
    p.write_text("{this is not valid json")
    store = PresetStore(path=p)
    presets = store.load()
    assert len(presets) == 1
    assert presets[0].name == "Seattle"
    assert (tmp_path / "presets.json.corrupt").exists()


def test_unparseable_preset_entry_backed_up_and_reseeded(tmp_path):
    p = _path(tmp_path)
    # Valid JSON but a preset that violates schema (city kind, empty value).
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "presets": [
                    {
                        "id": "abc",
                        "name": "bad",
                        "target": {"kind": "city", "value": ""},
                        "flags": {},
                        "is_default": True,
                    }
                ],
            }
        )
    )
    store = PresetStore(path=p)
    presets = store.load()
    assert presets[0].name == "Seattle"
    assert (tmp_path / "presets.json.corrupt").exists()


def test_empty_file_reseeds(tmp_path):
    p = _path(tmp_path)
    p.write_text("")
    store = PresetStore(path=p)
    presets = store.load()
    assert presets[0].name == "Seattle"


# ---- store: invariants ------------------------------------------------


def test_load_repairs_no_default(tmp_path):
    p = _path(tmp_path)
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "presets": [
                    {
                        "id": "a",
                        "name": "alpha",
                        "target": {"kind": "city", "value": "Seattle"},
                        "flags": {},
                        "is_default": False,
                    },
                    {
                        "id": "b",
                        "name": "beta",
                        "target": {"kind": "country", "value": "US"},
                        "flags": {},
                        "is_default": False,
                    },
                ],
            }
        )
    )
    store = PresetStore(path=p)
    presets = store.load()
    assert sum(1 for x in presets if x.is_default) == 1
    assert presets[0].is_default is True


def test_load_repairs_multiple_defaults(tmp_path):
    p = _path(tmp_path)
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "presets": [
                    {
                        "id": "a",
                        "name": "alpha",
                        "target": {"kind": "city", "value": "Seattle"},
                        "flags": {},
                        "is_default": True,
                    },
                    {
                        "id": "b",
                        "name": "beta",
                        "target": {"kind": "country", "value": "US"},
                        "flags": {},
                        "is_default": True,
                    },
                ],
            }
        )
    )
    store = PresetStore(path=p)
    presets = store.load()
    assert sum(1 for x in presets if x.is_default) == 1
    # The first one flagged wins.
    assert presets[0].name == "alpha"


def test_load_moves_default_to_top(tmp_path):
    p = _path(tmp_path)
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "presets": [
                    {
                        "id": "a",
                        "name": "alpha",
                        "target": {"kind": "city", "value": "Seattle"},
                        "flags": {},
                        "is_default": False,
                    },
                    {
                        "id": "b",
                        "name": "beta",
                        "target": {"kind": "country", "value": "US"},
                        "flags": {},
                        "is_default": True,
                    },
                ],
            }
        )
    )
    presets = PresetStore(path=p).load()
    assert presets[0].name == "beta"
    assert presets[1].name == "alpha"


# ---- store: CRUD ------------------------------------------------------


def test_add_preset_appends_below_default(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    store.load()
    added = store.add(
        name="NYC",
        target=PresetTarget(kind=TargetKind.CITY, value="New York"),
    )
    assert added.is_default is False
    all_p = store.list_all()
    assert [p.name for p in all_p] == ["Seattle", "NYC"]


def test_add_duplicate_name_raises(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    store.load()
    with pytest.raises(ValueError, match="already exists"):
        store.add(
            name="Seattle",
            target=PresetTarget(kind=TargetKind.CITY, value="Seattle"),
        )


def test_add_empty_name_raises(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    store.load()
    with pytest.raises(ValueError, match="non-empty"):
        store.add(
            name="   ",
            target=PresetTarget(kind=TargetKind.CITY, value="Seattle"),
        )


def test_update_preset(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    store.load()
    nyc = store.add(
        name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York")
    )
    updated = store.update(
        nyc.id,
        name="NYC P2P",
        flags=PresetFlags(p2p=True),
    )
    assert updated.name == "NYC P2P"
    assert updated.flags.p2p is True
    # Round-trips on disk.
    reloaded = PresetStore(path=_path(tmp_path)).load()
    nyc_after = [p for p in reloaded if p.id == nyc.id][0]
    assert nyc_after.name == "NYC P2P"


def test_update_duplicate_name_raises(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    store.load()
    nyc = store.add(
        name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York")
    )
    with pytest.raises(ValueError, match="already exists"):
        store.update(nyc.id, name="Seattle")


def test_delete_non_default(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    store.load()
    nyc = store.add(
        name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York")
    )
    store.delete(nyc.id)
    assert [p.name for p in store.list_all()] == ["Seattle"]


def test_delete_default_raises(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    presets = store.load()
    with pytest.raises(ValueError, match="default"):
        store.delete(presets[0].id)


def test_set_default_moves_to_top(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    store.load()
    nyc = store.add(
        name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York")
    )
    store.set_default(nyc.id)
    presets = store.list_all()
    assert presets[0].id == nyc.id
    assert presets[0].is_default is True
    assert sum(1 for p in presets if p.is_default) == 1


def test_set_default_idempotent(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    presets = store.load()
    seattle_id = presets[0].id
    result = store.set_default(seattle_id)
    assert result.id == seattle_id
    assert sum(1 for p in store.list_all() if p.is_default) == 1


def test_get_returns_none_for_missing(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    store.load()
    assert store.get("nonexistent") is None


def test_default_accessor(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    store.load()
    assert store.default().name == "Seattle"


# ---- atomic write -----------------------------------------------------


def test_no_partial_tmp_left_after_write(tmp_path):
    store = PresetStore(path=_path(tmp_path))
    store.load()
    store.add(
        name="NYC", target=PresetTarget(kind=TargetKind.CITY, value="New York")
    )
    leftovers = [
        f for f in os.listdir(tmp_path) if f.startswith(".presets.") and f.endswith(".tmp")
    ]
    assert leftovers == []
