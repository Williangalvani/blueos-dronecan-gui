import dronecan


def text_field(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return bytes(value).split(b"\x00", 1)[0].decode(errors="replace")
    except (TypeError, ValueError):
        return str(value)


def const_name(obj, field):
    try:
        return dronecan.value_to_constant_name(obj, field, keep_literal=True)
    except Exception:
        return str(getattr(obj, field, ""))


def render_union(union):
    field = dronecan.get_active_union_field(union)
    if not field or field == "empty":
        return None
    value = getattr(union, field)
    if field == "boolean_value":
        return bool(value)
    if field == "real_value":
        return round(float(value), 9)
    if field == "string_value":
        return text_field(value)
    if field == "integer_value":
        return int(value)
    return text_field(value)


def serialize_param(response, index):
    kind = dronecan.get_active_union_field(response.value) or "empty"
    return {
        "index": index,
        "name": text_field(response.name),
        "type": kind.replace("_value", ""),
        "value": render_union(response.value),
        "default": render_union(response.default_value),
        "min": render_union(response.min_value),
        "max": render_union(response.max_value),
    }


def value_union(kind, raw):
    union = dronecan.uavcan.protocol.param.Value()
    if kind == "integer":
        union.integer_value = int(raw)
    elif kind == "real":
        union.real_value = float(raw)
    elif kind == "boolean":
        if isinstance(raw, str):
            raw = raw.strip().lower() in ("1", "true", "yes", "on")
        union.boolean_value = int(bool(raw))
    elif kind == "string":
        union.string_value = "" if raw is None else str(raw)
    else:
        raise ValueError(f"unsupported parameter type {kind}")
    return union


def serialize_node(entry):
    status = entry.status
    info = entry.info
    row = {
        "id": entry.node_id,
        "health": const_name(status, "health") if status else "",
        "mode": const_name(status, "mode") if status else "",
        "uptime": int(status.uptime_sec) if status else None,
        "name": "",
        "software": "",
        "hardware": "",
    }
    if info:
        row["name"] = text_field(info.name)
        row["software"] = f"{int(info.software_version.major)}.{int(info.software_version.minor)}"
        row["hardware"] = f"{int(info.hardware_version.major)}.{int(info.hardware_version.minor)}"
    return row


def _decode_payload(transfer):
    payload = transfer.payload
    if payload is not None and not isinstance(payload, (bytes, bytearray)):
        datatype = dronecan.get_dronecan_data_type(payload)
        return datatype.full_name, dronecan.to_yaml(payload).strip()
    kind = (
        dronecan.dsdl.CompoundType.KIND_SERVICE
        if transfer.service_not_message
        else dronecan.dsdl.CompoundType.KIND_MESSAGE
    )
    datatype = dronecan.DATATYPES.get((transfer.data_type_id, kind))
    if datatype is None or payload is None:
        return f"dtid {transfer.data_type_id}", ""
    if transfer.service_not_message:
        message = datatype(_mode="request" if transfer.request_not_response else "response")
    else:
        message = datatype()
    message._unpack(dronecan.transport.bits_from_bytes(bytes(payload)), tao=not transfer.canfd)
    return datatype.full_name, dronecan.to_yaml(message).strip()


def serialize_transfer(transfer):
    try:
        name, text = _decode_payload(transfer)
    except Exception:
        name, text = f"dtid {getattr(transfer, 'data_type_id', '?')}", ""
    if len(text) > 600:
        text = text[:600] + "…"
    return {
        "dir": getattr(transfer, "direction", ""),
        "src": transfer.source_node_id,
        "dst": transfer.dest_node_id,
        "service": bool(transfer.service_not_message),
        "type": name,
        "text": text,
    }
