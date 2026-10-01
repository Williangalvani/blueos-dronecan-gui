from types import SimpleNamespace

import dronecan

from app.serialize import render_union, serialize_node, serialize_param, serialize_transfer, value_union


def test_render_param_unions():
    empty = dronecan.uavcan.protocol.param.Value()
    assert render_union(empty) is None
    assert render_union(value_union("integer", 7)) == 7
    assert render_union(value_union("real", 1.25)) == 1.25
    assert render_union(value_union("boolean", True)) is True
    assert render_union(value_union("string", "abc")) == "abc"


def test_param_row():
    response = dronecan.uavcan.protocol.param.GetSet.Response()
    response.name = "GAIN"
    response.value.integer_value = 4
    row = serialize_param(response, 2)
    assert row["name"] == "GAIN"
    assert row["index"] == 2
    assert row["type"] == "integer"
    assert row["value"] == 4


def test_packed_node_status_decodes():
    message = dronecan.uavcan.protocol.NodeStatus()
    message.uptime_sec = 3
    message.health = message.HEALTH_OK
    message.mode = message.MODE_OPERATIONAL
    transfer = dronecan.transport.Transfer(payload=message, source_node_id=127)
    row = serialize_transfer(transfer)
    assert row["type"] == "uavcan.protocol.NodeStatus"
    assert "uptime_sec: 3" in row["text"]
    assert row["src"] == 127


def test_node_without_info():
    status = dronecan.uavcan.protocol.NodeStatus()
    status.mode = status.MODE_OPERATIONAL
    status.health = status.HEALTH_WARNING
    status.uptime_sec = 12
    row = serialize_node(SimpleNamespace(node_id=7, status=status, info=None))
    assert row["id"] == 7
    assert row["uptime"] == 12
    assert "WARNING" in row["health"]
    assert "OPERATIONAL" in row["mode"]
    assert row["name"] == ""
