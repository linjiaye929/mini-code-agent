from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import cast

from pydantic import JsonValue

type FrozenJsonValue = (
    None
    | bool
    | int
    | float
    | str
    | tuple["FrozenJsonValue", ...]
    | Mapping[str, "FrozenJsonValue"]
)


def freeze_json(value: object) -> FrozenJsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return MappingProxyType({str(key): freeze_json(item) for key, item in mapping.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence = cast(Sequence[object], value)
        return tuple(freeze_json(item) for item in sequence)
    raise TypeError(f"Unsupported JSON value type: {type(value).__name__}")


def freeze_json_mapping(
    value: Mapping[str, object],
) -> Mapping[str, FrozenJsonValue]:
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("JSON mapping invariant failed")
    return frozen


def thaw_json(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def thaw_json_mapping(
    value: Mapping[str, FrozenJsonValue],
) -> dict[str, JsonValue]:
    return {key: thaw_json(item) for key, item in value.items()}
