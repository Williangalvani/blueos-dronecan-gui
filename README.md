# DroneCAN GUI

BlueOS extension that configures DroneCAN nodes through the autopilot. The container connects with `mavcan:udpout:host.docker.internal:14550`, the same tunnel as the desktop tool's `mavcan:udpout:<vehicle>:14550`. It does not open the vehicle CAN interfaces.

Local run:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
DRONECAN_MAVLINK=udpout:127.0.0.1:14550 PORT=8080 .venv/bin/python -m app.main
```

Then open http://127.0.0.1:8080/.
