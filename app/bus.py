import os
import queue
import threading
import time
from collections import deque
from pathlib import Path

import dronecan

from app.serialize import serialize_node, serialize_param, serialize_transfer, text_field, value_union

DEFAULT_URL = os.environ.get("DRONECAN_MAVLINK", "udpout:host.docker.internal:14550")
NODE_NAME = "org.dronecan.gui_tool"
REQUEST_PRIORITY = 30
MAX_SPIN_ERRORS = 50
PARAM_RETRIES = 3


def _drop(handle):
    if handle is None:
        return
    remover = getattr(handle, "try_remove", None) or getattr(handle, "remove", None)
    if remover is None:
        return
    try:
        remover()
    except Exception:
        pass


class Bus:
    def __init__(self):
        self._lock = threading.Lock()
        self._listeners = []
        self._jobs = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._gate = threading.Lock()
        self._path_map = {}
        self._param_gen = 0
        self._spin_errors = 0
        self._last_publish = 0.0
        self.node = None
        self.monitor = None
        self.file_server = None
        self.allocator = None
        self._view = {
            "connected": False,
            "connecting": False,
            "error": None,
            "mavlink_url": DEFAULT_URL,
            "node_id": None,
            "bus": 1,
            "target_system": 0,
            "allocator": False,
            "nodes": [],
            "logs": deque(maxlen=100),
            "frames": deque(maxlen=200),
            "params": None,
            "firmware": None,
        }

    def snapshot(self):
        with self._lock:
            return self._copy_view()

    def add_listener(self, listener):
        with self._lock:
            self._listeners.append(listener)
            snap = self._copy_view()
        listener(snap)

    def remove_listener(self, listener):
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def connect(self, url, node_id, bus, target_system, signing_key):
        url = (url or DEFAULT_URL).strip()
        if url.startswith("mavcan:"):
            url = url[len("mavcan:") :]
        if not url:
            raise ValueError("MAVLink URL is empty")
        node_id = int(node_id)
        if not 1 <= node_id <= 127:
            raise ValueError("node id must be 1..127")
        with self._gate:
            self.disconnect()
            self._stop = threading.Event()
            settings = {
                "url": url,
                "node_id": node_id,
                "bus": int(bus),
                "target_system": int(target_system),
                "signing_key": signing_key or "",
            }
            self._thread = threading.Thread(target=self._run, args=(settings,), name="dronecan", daemon=True)
            self._thread.start()

    def disconnect(self):
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=3)
        self._thread = None

    def set_allocator(self, enabled):
        def job():
            if enabled and self.allocator is None:
                self.allocator = dronecan.app.dynamic_node_id.CentralizedServer(self.node, self.monitor)
            elif not enabled and self.allocator is not None:
                self.allocator.close()
                self.allocator = None
            self._set(allocator=bool(self.allocator))

        self._require()
        self._submit(job)

    def fetch_params(self, node_id):
        self._require()
        self._param_gen += 1
        generation = self._param_gen
        state = {"node_id": int(node_id), "index": 0, "items": [], "done": False, "error": None}
        attempts = {"left": PARAM_RETRIES}
        self._set(params=state)

        def step():
            if generation != self._param_gen or self.node is None:
                return
            request = dronecan.uavcan.protocol.param.GetSet.Request(index=state["index"])

            def callback(event):
                if generation != self._param_gen:
                    return
                if event is None and attempts["left"] > 0:
                    attempts["left"] -= 1
                    self._submit(step)
                    return
                if event is None:
                    state["error"] = f"timed out at parameter {state['index']}"
                    state["done"] = True
                    self._set(params=state)
                    self._publish(force=True)
                    return
                name = text_field(event.response.name)
                if not name:
                    state["done"] = True
                    self._set(params=state)
                    self._publish(force=True)
                    return
                state["items"] = state["items"] + [serialize_param(event.response, state["index"])]
                state["index"] += 1
                attempts["left"] = PARAM_RETRIES
                self._set(params=state)
                self._submit(step)

            self.node.request(request, int(node_id), callback, priority=REQUEST_PRIORITY)

        self._submit(step)

    def set_param(self, node_id, name, kind, raw):
        def make_request():
            return dronecan.uavcan.protocol.param.GetSet.Request(name=name, value=value_union(kind, raw, name))

        event = self.request(make_request, int(node_id))
        return serialize_param(event.response, None)

    def opcode(self, node_id, opcode):
        def make_request():
            request = dronecan.uavcan.protocol.param.ExecuteOpcode.Request()
            if opcode == "save":
                request.opcode = request.OPCODE_SAVE
            elif opcode == "erase":
                request.opcode = request.OPCODE_ERASE
            else:
                raise ValueError("opcode must be save or erase")
            return request

        event = self.request(make_request, int(node_id))
        return text_field(dronecan.to_yaml(event.response))

    def restart(self, node_id):
        def make_request():
            magic = dronecan.uavcan.protocol.RestartNode.Request().MAGIC_NUMBER
            return dronecan.uavcan.protocol.RestartNode.Request(magic_number=magic)

        event = self.request(make_request, int(node_id), timeout=3)
        return text_field(dronecan.to_yaml(event.response))

    def begin_firmware(self, node_id, path):
        self._require()
        path = str(path)
        remote = os.path.basename(path)
        self._path_map[remote] = path
        node_id = int(node_id)

        def start():
            self._firmware_update(node_id, remote)

        self._submit(start)

    def request(self, make_request, node_id, timeout=2.0):
        self._require()
        done = threading.Event()
        box = {}

        def start():
            try:
                payload = make_request()

                def callback(event):
                    box["event"] = event
                    done.set()

                self.node.request(payload, node_id, callback, priority=REQUEST_PRIORITY)
            except Exception as exc:
                box["error"] = exc
                done.set()

        self._submit(start)
        if not done.wait(timeout):
            raise TimeoutError("request timed out")
        if "error" in box:
            raise box["error"]
        event = box.get("event")
        if event is None:
            raise TimeoutError("node did not respond")
        return event

    def _require(self):
        if self.node is None or not self.snapshot()["connected"]:
            raise RuntimeError("not connected")

    def _submit(self, job):
        self._jobs.put(job)

    def _run(self, settings):
        try:
            self._set(
                connecting=True,
                connected=False,
                error=None,
                mavlink_url=settings["url"],
                node_id=settings["node_id"],
                bus=settings["bus"],
                target_system=settings["target_system"],
                nodes=[],
                logs=deque(maxlen=100),
                frames=deque(maxlen=200),
                params=None,
                firmware=None,
                allocator=False,
            )
            self._publish(force=True)
            node_info = dronecan.uavcan.protocol.GetNodeInfo.Response()
            node_info.name = NODE_NAME
            self.node = dronecan.make_node(
                "mavcan:" + settings["url"],
                node_id=settings["node_id"],
                node_info=node_info,
                mode=dronecan.uavcan.protocol.NodeStatus().MODE_OPERATIONAL,
                bus_number=settings["bus"],
                mavlink_target_system=settings["target_system"],
            )
            if settings["signing_key"]:
                self.node.can_driver.set_signing_passphrase(settings["signing_key"])
            self.monitor = dronecan.app.node_monitor.NodeMonitor(self.node)
            self.file_server = dronecan.app.file_server.FileServer(self.node, path_map=self._path_map)
            self.node.add_handler(dronecan.uavcan.protocol.debug.LogMessage, self._on_log)
            self.node.add_transfer_hook(self._on_transfer)
            self._set(connected=True, connecting=False)
            self._publish(force=True)
            while not self._stop.is_set():
                self._drain()
                try:
                    self.node.spin(0.05)
                    self._spin_errors = 0
                except Exception as exc:
                    self._spin_errors += 1
                    self._set(error=str(exc))
                    if self._spin_errors >= MAX_SPIN_ERRORS:
                        break
                self._refresh_and_publish()
        except Exception as exc:
            self._set(error=str(exc), connected=False, connecting=False)
        finally:
            self._close_node()
            self._set(connected=False, connecting=False, allocator=False)
            self._publish(force=True)

    def _firmware_update(self, node_id, remote):
        remaining = 4
        handles = {}
        finished = False
        self._set(firmware={"node_id": node_id, "message": f"requesting {remote}", "done": False})
        self._publish(force=True)

        def finish(message):
            nonlocal finished
            if finished:
                return
            finished = True
            _drop(handles.get("defer"))
            _drop(handles.get("status"))
            handles.clear()
            self._set(firmware={"node_id": node_id, "message": message, "done": True})
            self._publish(force=True)

        def on_status(event):
            message = event.message
            if event.transfer.source_node_id == node_id and message.mode == message.MODE_SOFTWARE_UPDATE:
                finish("node entered software update")

        def send():
            nonlocal remaining
            if finished:
                return
            handles["defer"] = None
            if remaining <= 0:
                finish("no acknowledgement")
                return
            remaining -= 1
            request = dronecan.uavcan.protocol.file.BeginFirmwareUpdate.Request(
                source_node_id=self.node.node_id,
                image_file_remote_path=dronecan.uavcan.protocol.file.Path(path=remote),
            )

            def on_response(event):
                if finished:
                    return
                if event is None:
                    self._set(firmware={"node_id": node_id, "message": "request timed out", "done": False})
                    handles["defer"] = self.node.defer(2, send)
                    return
                text = dronecan.to_yaml(event.response).strip()
                code = int(event.response.error)
                if code in (event.response.ERROR_OK, event.response.ERROR_IN_PROGRESS):
                    finish(text)
                else:
                    self._set(firmware={"node_id": node_id, "message": text, "done": False})
                    handles["defer"] = self.node.defer(2, send)

            self._set(firmware={"node_id": node_id, "message": f"sending ({remaining} left)", "done": False})
            self.node.request(request, node_id, on_response, priority=REQUEST_PRIORITY)

        handles["status"] = self.node.add_handler(dronecan.uavcan.protocol.NodeStatus, on_status)
        send()

    def _on_log(self, event):
        row = {
            "node": event.transfer.source_node_id,
            "level": int(event.message.level.value),
            "source": text_field(event.message.source),
            "text": text_field(event.message.text),
        }
        with self._lock:
            self._view["logs"].append(row)

    def _on_transfer(self, transfer):
        try:
            row = serialize_transfer(transfer)
        except Exception:
            return
        with self._lock:
            self._view["frames"].append(row)

    def _refresh_and_publish(self):
        nodes = []
        if self.monitor is not None:
            nodes = [serialize_node(entry) for entry in self.monitor.find_all(lambda _entry: True)]
            nodes.sort(key=lambda row: row["id"] or 0)
        now = time.monotonic()
        with self._lock:
            self._view["nodes"] = nodes
            if now - self._last_publish < 0.25:
                return
            self._last_publish = now
            snap = self._copy_view()
        self._emit(snap)

    def _publish(self, force=False):
        with self._lock:
            if not force and time.monotonic() - self._last_publish < 0.25:
                return
            self._last_publish = time.monotonic()
            snap = self._copy_view()
        self._emit(snap)

    def _emit(self, snap):
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(snap)
            except Exception:
                pass

    def _drain(self):
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            try:
                job()
            except Exception as exc:
                self._set(error=str(exc))

    def _close_node(self):
        for closer in (self.allocator, self.file_server, self.node):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception:
                pass
        self.allocator = None
        self.file_server = None
        self.monitor = None
        self.node = None

    def _set(self, **fields):
        with self._lock:
            self._view.update(fields)

    def _copy_view(self):
        view = dict(self._view)
        view["nodes"] = list(self._view["nodes"])
        view["logs"] = list(self._view["logs"])
        view["frames"] = list(self._view["frames"])
        if self._view["params"] is not None:
            params = dict(self._view["params"])
            params["items"] = list(params["items"])
            view["params"] = params
        if self._view["firmware"] is not None:
            view["firmware"] = dict(self._view["firmware"])
        return view


def firmware_dir():
    path = Path(os.environ.get("DRONECAN_DATA", "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path
